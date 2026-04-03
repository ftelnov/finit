#[allow(dead_code)]
mod a2a;
mod agui;
mod api;
mod config;
#[allow(dead_code)]
mod db;
mod metrics;
mod supervisor;

use axum::routing::{delete, get, post};
use axum::Router;
use sqlx::postgres::PgPoolOptions;
use std::time::Duration;
use tower_http::cors::{Any, CorsLayer};
use tower_http::trace::TraceLayer;
use tracing_subscriber::EnvFilter;

/// Shared application state, passed to all handlers via axum's State extractor.
#[derive(Clone)]
pub struct AppState {
    pub pool: sqlx::PgPool,
    pub config: config::Config,
    pub a2a_client: a2a::A2AClient,
    pub event_bus: agui::EventBus,
    pub metrics: metrics::Metrics,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .json()
        .init();

    let config = config::Config::from_env();

    tracing::info!(
        listen_addr = %config.listen_addr,
        "starting finit orchestrator"
    );

    // Connect to PostgreSQL
    let pool = PgPoolOptions::new()
        .max_connections(config.db_max_connections)
        .acquire_timeout(Duration::from_secs(5))
        .connect(&config.database_url)
        .await?;

    tracing::info!("connected to PostgreSQL");

    // Initialize components
    let a2a_client = a2a::A2AClient::new(Duration::from_secs(config.phase_timeout_s as u64));
    let event_bus = agui::EventBus::new(pool.clone());
    let app_metrics = metrics::Metrics::new();

    let state = AppState {
        pool: pool.clone(),
        config: config.clone(),
        a2a_client,
        event_bus,
        metrics: app_metrics,
    };

    // Spawn agent health check background task
    spawn_health_checker(state.clone());

    // Build router
    let app = build_router(state);

    // Start server
    let listener = tokio::net::TcpListener::bind(&config.listen_addr).await?;
    tracing::info!(addr = %config.listen_addr, "listening");
    axum::serve(listener, app).await?;

    Ok(())
}

fn build_router(state: AppState) -> Router {
    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    Router::new()
        // Task endpoints
        .route("/api/tasks", post(api::create_task))
        .route("/api/tasks", get(api::list_tasks))
        .route("/api/tasks/:id", get(api::get_task))
        .route("/api/tasks/:id", delete(api::cancel_task))
        .route("/api/tasks/:id/input", post(api::task_input))
        // AG-UI SSE endpoint
        .route("/ag-ui/tasks/:id/events", get(api::task_events_sse))
        // Agent endpoints
        .route("/api/agents", post(api::register_agent))
        .route("/api/agents", get(api::list_agents))
        .route("/api/agents/:id", get(api::get_agent))
        .route("/api/agents/:id", delete(api::delete_agent))
        // System endpoints
        .route("/health", get(api::health_check))
        .route("/metrics", get(metrics::metrics_handler))
        .layer(TraceLayer::new_for_http())
        .layer(cors)
        .with_state(state)
}

/// Spawn a background task that periodically checks agent health.
fn spawn_health_checker(state: AppState) {
    let interval = Duration::from_secs(state.config.health_check_interval_s);
    let timeout = Duration::from_secs(state.config.health_check_timeout_s);
    let threshold = state.config.unhealthy_threshold;

    tokio::spawn(async move {
        // Track consecutive failures per agent
        let mut failure_counts: std::collections::HashMap<String, i32> =
            std::collections::HashMap::new();

        loop {
            tokio::time::sleep(interval).await;

            let agents = match db::list_agents(&state.pool).await {
                Ok(agents) => agents,
                Err(e) => {
                    tracing::warn!("failed to list agents for health check: {}", e);
                    continue;
                }
            };

            for agent in agents {
                let healthy = state
                    .a2a_client
                    .check_health(&agent.url, timeout)
                    .await
                    .unwrap_or(false);

                if healthy {
                    failure_counts.remove(&agent.id);
                    if agent.status != "healthy" {
                        tracing::info!(agent_id = %agent.id, "agent recovered");
                    }
                    let _ =
                        db::update_agent_health(&state.pool, &agent.id, "healthy").await;
                } else {
                    let count = failure_counts.entry(agent.id.clone()).or_insert(0);
                    *count += 1;

                    if *count >= threshold {
                        if agent.status != "unhealthy" {
                            tracing::warn!(
                                agent_id = %agent.id,
                                failures = *count,
                                "agent marked unhealthy"
                            );
                        }
                        let _ =
                            db::update_agent_health(&state.pool, &agent.id, "unhealthy").await;
                    }
                }
            }
        }
    });
}
