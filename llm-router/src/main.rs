#![allow(dead_code)]

mod auth;
mod cache;
mod circuit_breaker;
mod config;
mod guardrails;
mod management;
mod metrics;
mod provider;
mod proxy;
mod routing;

use config::RouterConfig;
use guardrails::Guardrails;
use pingora::server::configuration::Opt;
use pingora::server::Server;
use provider::ProviderPool;
use proxy::LlmRouterProxy;
use std::sync::Arc;
use std::time::Duration;

fn main() {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .json()
        .init();

    // Initialize Prometheus metrics
    metrics::init_metrics();

    // Load configuration
    let config_path =
        std::env::var("CONFIG_PATH").unwrap_or_else(|_| "/etc/finit/router.yaml".to_string());

    let config = match RouterConfig::load(&config_path) {
        Ok(c) => c,
        Err(e) => {
            tracing::error!(error = %e, path = %config_path, "failed to load configuration");
            std::process::exit(1);
        }
    };

    tracing::info!(
        listen = %config.listen,
        providers = config.providers.len(),
        strategy = %config.balancing.strategy,
        "loaded configuration"
    );

    // Create the routing strategy
    let strategy = routing::create_strategy(&config.balancing.strategy);
    tracing::info!(strategy = %strategy.name(), "initialized routing strategy");

    // Create the provider pool
    let pool = Arc::new(ProviderPool::new(
        &config.providers,
        strategy,
        config.circuit_breaker.clone(),
    ));

    // Create reqwest HTTP client for upstream calls
    let http_client = reqwest::Client::builder()
        .pool_max_idle_per_host(100)
        .tcp_keepalive(Some(Duration::from_secs(60)))
        .connect_timeout(Duration::from_secs(10))
        .build()
        .expect("failed to build HTTP client");

    // Optional: PostgreSQL for budget tracking (pool created lazily)
    let db_url = get_db_url();

    // Create the LLM cache
    let llm_cache = cache::LlmCache::new(config.cache.ttl_seconds)
        .with_semantic_threshold(config.cache.semantic_threshold);

    // Create guardrails
    let guardrails = Arc::new(Guardrails::new(
        config.guardrails.prompt_injection,
        config.guardrails.secret_scan,
    ));
    tracing::info!(
        prompt_injection = config.guardrails.prompt_injection,
        secret_scan = config.guardrails.secret_scan,
        "guardrails initialized"
    );

    // Create the proxy service
    let llm_proxy = LlmRouterProxy {
        pool: pool.clone(),
        http_client,
        db_url,
        db_pool: tokio::sync::OnceCell::new(),
        cache: llm_cache,
        cache_enabled: config.cache.enabled,
        guardrails,
    };

    // Parse Pingora options
    let opt = Opt::parse_args();
    let mut server = Server::new(Some(opt)).unwrap();
    server.bootstrap();

    // Create the HTTP proxy service
    let mut proxy_service =
        pingora::proxy::http_proxy_service(&server.configuration, llm_proxy);

    // Parse the listen address
    let listen_addr = normalize_listen_addr(&config.listen);
    proxy_service.add_tcp(&listen_addr);
    tracing::info!(addr = %listen_addr, "LLM Router listening");

    server.add_service(proxy_service);

    // Start background health checker
    let health_pool = pool.clone();
    let health_interval = pool.min_health_check_interval();
    let bg_health = pingora::services::background::background_service(
        "health-checker",
        HealthCheckService {
            pool: health_pool,
            interval: health_interval,
        },
    );
    server.add_service(bg_health);

    // Start background cache cleanup (expires old entries every 5 minutes)
    let bg_cache = pingora::services::background::background_service(
        "cache-cleanup",
        CacheCleanupService {
            cache: cache::LlmCache::new(config.cache.ttl_seconds),
            db_url: get_db_url(),
            interval: Duration::from_secs(300),
        },
    );
    server.add_service(bg_cache);

    // Run the server (Pingora handles SIGTERM/SIGINT with graceful drain)
    tracing::info!("starting Pingora server");
    server.run_forever();
}

/// Normalize the listen address from config format (":8081") to Pingora format ("0.0.0.0:8081")
fn normalize_listen_addr(addr: &str) -> String {
    if addr.starts_with(':') {
        format!("0.0.0.0{}", addr)
    } else {
        addr.to_string()
    }
}

/// Get the database URL from environment. Pool will be created lazily on first use.
fn get_db_url() -> Option<String> {
    let url = std::env::var("DATABASE_URL").ok()?;
    tracing::info!("PostgreSQL URL configured, pool will connect on first request");
    Some(url)
}

/// Background service for periodic health checks
struct HealthCheckService {
    pool: Arc<ProviderPool>,
    interval: Duration,
}

#[async_trait::async_trait]
impl pingora::services::background::BackgroundService for HealthCheckService {
    async fn start(&self, mut shutdown: tokio::sync::watch::Receiver<bool>) {
        tracing::info!(
            interval_s = self.interval.as_secs(),
            "starting health check background service"
        );

        let mut interval = tokio::time::interval(self.interval);

        loop {
            tokio::select! {
                _ = interval.tick() => {
                    self.pool.health_check_all().await;
                }
                _ = shutdown.changed() => {
                    tracing::info!("health check service shutting down");
                    return;
                }
            }
        }
    }
}

/// Background service for periodic cache cleanup (expired entries).
struct CacheCleanupService {
    cache: cache::LlmCache,
    db_url: Option<String>,
    interval: Duration,
}

#[async_trait::async_trait]
impl pingora::services::background::BackgroundService for CacheCleanupService {
    async fn start(&self, mut shutdown: tokio::sync::watch::Receiver<bool>) {
        let db_url = match &self.db_url {
            Some(url) => url,
            None => {
                tracing::info!("cache cleanup disabled (no DATABASE_URL)");
                return;
            }
        };

        let db_pool = match sqlx::postgres::PgPoolOptions::new()
            .max_connections(1)
            .connect(db_url)
            .await
        {
            Ok(pool) => pool,
            Err(e) => {
                tracing::warn!(error = %e, "cache cleanup: failed to connect to DB");
                return;
            }
        };

        tracing::info!(
            interval_s = self.interval.as_secs(),
            "starting cache cleanup background service"
        );

        let mut interval = tokio::time::interval(self.interval);

        loop {
            tokio::select! {
                _ = interval.tick() => {
                    match self.cache.cleanup_expired(&db_pool).await {
                        Ok(n) if n > 0 => tracing::info!(deleted = n, "cleaned up expired cache entries"),
                        Ok(_) => {}
                        Err(e) => tracing::warn!(error = %e, "cache cleanup failed"),
                    }
                }
                _ = shutdown.changed() => {
                    tracing::info!("cache cleanup service shutting down");
                    // Final cleanup before exit
                    let _ = self.cache.cleanup_expired(&db_pool).await;
                    db_pool.close().await;
                    return;
                }
            }
        }
    }
}
