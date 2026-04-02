use crate::circuit_breaker::CircuitBreaker;
use crate::config::{CircuitBreakerConfig, ModelConfig, ProviderConfig};
use crate::metrics;
use crate::routing::{RouteRequest, RoutingStrategy};
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

/// Runtime state for a single provider
pub struct ProviderState {
    pub id: String,
    pub name: String,
    pub url: String,
    pub api_key: Option<String>,
    pub models: HashMap<String, ModelConfig>,
    pub weight: u32,
    pub timeout: Duration,
    pub health_check_interval: Duration,
    pub healthy: std::sync::atomic::AtomicBool,
    pub active_requests: AtomicUsize,
    pub circuit_breaker: CircuitBreaker,
    ewma_latency_ms_raw: AtomicU64,
    pub total_requests: AtomicU64,
}

impl ProviderState {
    pub fn from_config(config: &ProviderConfig, cb_config: &CircuitBreakerConfig) -> Self {
        Self {
            id: config.name.clone(),
            name: config.name.clone(),
            url: config.url.clone(),
            api_key: config.api_key.clone(),
            models: config.models.clone(),
            weight: config.weight,
            timeout: Duration::from_millis(config.timeout_ms),
            health_check_interval: Duration::from_secs(config.health_check_interval_s),
            healthy: std::sync::atomic::AtomicBool::new(true),
            active_requests: AtomicUsize::new(0),
            circuit_breaker: CircuitBreaker::new(
                cb_config.failure_threshold,
                cb_config.failure_window_s,
                cb_config.cooldown_s,
                cb_config.half_open_requests,
            ),
            ewma_latency_ms_raw: AtomicU64::new(0),
            total_requests: AtomicU64::new(0),
        }
    }

    /// Get the EWMA latency in milliseconds
    pub fn ewma_latency_ms(&self) -> f64 {
        f64::from_bits(self.ewma_latency_ms_raw.load(Ordering::Relaxed))
    }

    /// Update the EWMA latency with a new sample (alpha=0.3)
    pub fn update_ewma_latency(&self, latency_ms: f64) {
        const ALPHA: f64 = 0.3;
        loop {
            let old_bits = self.ewma_latency_ms_raw.load(Ordering::Relaxed);
            let old_val = f64::from_bits(old_bits);
            let new_val = if old_val == 0.0 {
                latency_ms
            } else {
                ALPHA * latency_ms + (1.0 - ALPHA) * old_val
            };
            let new_bits = new_val.to_bits();
            if self
                .ewma_latency_ms_raw
                .compare_exchange_weak(old_bits, new_bits, Ordering::Relaxed, Ordering::Relaxed)
                .is_ok()
            {
                break;
            }
        }
    }

    /// Check if this provider is available for requests
    pub fn is_available(&self) -> bool {
        self.healthy.load(Ordering::Relaxed) && self.circuit_breaker.allow_request()
    }

    /// Check if this provider serves the given model
    pub fn serves_model(&self, model: &str) -> bool {
        self.models.contains_key(model)
    }

    /// Get the chat completions URL for this provider
    pub fn chat_completions_url(&self) -> String {
        let base = self.url.trim_end_matches('/');
        format!("{}/chat/completions", base)
    }

    /// Serialize to JSON for the management API
    pub fn to_info(&self) -> ProviderInfo {
        ProviderInfo {
            id: self.id.clone(),
            name: self.name.clone(),
            url: self.url.clone(),
            models: self.models.keys().cloned().collect(),
            weight: self.weight,
            healthy: self.healthy.load(Ordering::Relaxed),
            active_requests: self.active_requests.load(Ordering::Relaxed),
            ewma_latency_ms: self.ewma_latency_ms(),
            circuit_breaker_state: format!("{:?}", self.circuit_breaker.state()),
            total_requests: self.total_requests.load(Ordering::Relaxed),
        }
    }
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ProviderInfo {
    pub id: String,
    pub name: String,
    pub url: String,
    pub models: Vec<String>,
    pub weight: u32,
    pub healthy: bool,
    pub active_requests: usize,
    pub ewma_latency_ms: f64,
    pub circuit_breaker_state: String,
    pub total_requests: u64,
}

/// Request body for registering a new provider
#[derive(Debug, Deserialize)]
pub struct RegisterProviderRequest {
    pub name: String,
    pub url: String,
    pub api_key: Option<String>,
    #[serde(default = "default_weight")]
    pub weight: u32,
    #[serde(default)]
    pub models: HashMap<String, ModelConfig>,
    #[serde(default = "default_timeout")]
    pub timeout_ms: u64,
    #[serde(default = "default_health_check")]
    pub health_check_interval_s: u64,
}

fn default_weight() -> u32 {
    1
}
fn default_timeout() -> u64 {
    30_000
}
fn default_health_check() -> u64 {
    10
}

/// Manages the pool of LLM providers
pub struct ProviderPool {
    providers: RwLock<Vec<Arc<ProviderState>>>,
    strategy: Box<dyn RoutingStrategy>,
    cb_config: CircuitBreakerConfig,
    http_client: reqwest::Client,
}

impl ProviderPool {
    pub fn new(
        configs: &[ProviderConfig],
        strategy: Box<dyn RoutingStrategy>,
        cb_config: CircuitBreakerConfig,
    ) -> Self {
        let providers: Vec<Arc<ProviderState>> = configs
            .iter()
            .map(|c| Arc::new(ProviderState::from_config(c, &cb_config)))
            .collect();

        // Initialize health metrics
        for p in &providers {
            metrics::LLM_PROVIDER_HEALTH
                .with_label_values(&[&p.name])
                .set(1.0);
            metrics::LLM_ACTIVE_REQUESTS
                .with_label_values(&[&p.name])
                .set(0.0);
            metrics::LLM_CIRCUIT_BREAKER_STATE
                .with_label_values(&[&p.name])
                .set(0.0);
        }

        let http_client = reqwest::Client::builder()
            .timeout(Duration::from_secs(5))
            .build()
            .expect("failed to build HTTP client for health checks");

        Self {
            providers: RwLock::new(providers),
            strategy,
            cb_config,
            http_client,
        }
    }

    /// Select a provider for the given model.
    /// Returns the selected provider and its index in the filtered pool.
    pub fn select_provider(&self, model: &str, prompt_hash: Option<u64>) -> Option<Arc<ProviderState>> {
        let providers = self.providers.read();

        // Filter to healthy providers that serve the requested model
        let candidates: Vec<ProviderState> = providers
            .iter()
            .filter(|p| p.serves_model(model) && p.is_available())
            .map(|p| {
                // Create a lightweight view for the strategy
                // We pass references through ProviderState fields
                ProviderState {
                    id: p.id.clone(),
                    name: p.name.clone(),
                    url: p.url.clone(),
                    api_key: p.api_key.clone(),
                    models: p.models.clone(),
                    weight: p.weight,
                    timeout: p.timeout,
                    health_check_interval: p.health_check_interval,
                    healthy: std::sync::atomic::AtomicBool::new(true),
                    active_requests: AtomicUsize::new(
                        p.active_requests.load(Ordering::Relaxed),
                    ),
                    circuit_breaker: CircuitBreaker::new(0, 0, 0, 0), // dummy for routing
                    ewma_latency_ms_raw: AtomicU64::new(p.ewma_latency_ms_raw.load(Ordering::Relaxed)),
                    total_requests: AtomicU64::new(0),
                }
            })
            .collect();

        if candidates.is_empty() {
            return None;
        }

        let request = RouteRequest {
            model: model.to_string(),
            prompt_hash,
        };

        let selected_idx = self.strategy.select(&candidates, &request)?;
        let selected_name = &candidates[selected_idx].name;

        // Find the actual Arc<ProviderState> by name
        providers
            .iter()
            .find(|p| p.name == *selected_name)
            .cloned()
    }

    /// Select a different provider from the one that failed (for failover)
    pub fn select_failover(&self, model: &str, exclude_name: &str) -> Option<Arc<ProviderState>> {
        let providers = self.providers.read();
        providers
            .iter()
            .filter(|p| p.serves_model(model) && p.is_available() && p.name != exclude_name)
            .min_by_key(|p| p.active_requests.load(Ordering::Relaxed))
            .cloned()
    }

    /// Get all providers
    pub fn providers(&self) -> Vec<Arc<ProviderState>> {
        self.providers.read().clone()
    }

    /// Check if at least one provider is healthy
    pub fn has_healthy_provider(&self) -> bool {
        self.providers
            .read()
            .iter()
            .any(|p| p.healthy.load(Ordering::Relaxed))
    }

    /// Register a new provider dynamically
    pub fn register_provider(&self, req: RegisterProviderRequest) -> Arc<ProviderState> {
        let config = ProviderConfig {
            name: req.name,
            url: req.url,
            api_key: req.api_key,
            weight: req.weight,
            models: req.models,
            timeout_ms: req.timeout_ms,
            health_check_interval_s: req.health_check_interval_s,
        };

        let provider = Arc::new(ProviderState::from_config(&config, &self.cb_config));

        metrics::LLM_PROVIDER_HEALTH
            .with_label_values(&[&provider.name])
            .set(1.0);
        metrics::LLM_ACTIVE_REQUESTS
            .with_label_values(&[&provider.name])
            .set(0.0);
        metrics::LLM_CIRCUIT_BREAKER_STATE
            .with_label_values(&[&provider.name])
            .set(0.0);

        self.providers.write().push(provider.clone());
        tracing::info!(provider = %config.name, "registered new provider");

        provider
    }

    /// Remove a provider by ID
    pub fn remove_provider(&self, id: &str) -> bool {
        let mut providers = self.providers.write();
        let initial_len = providers.len();
        providers.retain(|p| p.id != id);
        let removed = providers.len() < initial_len;
        if removed {
            tracing::info!(provider = %id, "removed provider");
        }
        removed
    }

    /// Run health checks for all providers
    pub async fn health_check_all(&self) {
        let providers = self.providers.read().clone();
        for provider in &providers {
            let was_healthy = provider.healthy.load(Ordering::Relaxed);
            let is_healthy = self.check_provider_health(provider).await;
            provider.healthy.store(is_healthy, Ordering::Relaxed);

            let health_val = if is_healthy { 1.0 } else { 0.0 };
            metrics::LLM_PROVIDER_HEALTH
                .with_label_values(&[&provider.name])
                .set(health_val);

            metrics::LLM_CIRCUIT_BREAKER_STATE
                .with_label_values(&[&provider.name])
                .set(provider.circuit_breaker.state().as_f64());

            if was_healthy && !is_healthy {
                tracing::warn!(provider = %provider.name, "provider became unhealthy");
            } else if !was_healthy && is_healthy {
                tracing::info!(provider = %provider.name, "provider recovered");
            }
        }
    }

    /// Check a single provider's health
    async fn check_provider_health(&self, provider: &ProviderState) -> bool {
        let base_url = provider.url.trim_end_matches('/');

        // Try /health first, then /v1/models as fallback
        let health_url = format!("{}/health", base_url.trim_end_matches("/v1"));
        match self
            .http_client
            .get(&health_url)
            .timeout(Duration::from_secs(5))
            .send()
            .await
        {
            Ok(resp) if resp.status().is_success() => return true,
            _ => {}
        }

        // Fallback: try /v1/models (or /models if base already includes /v1)
        let models_url = if base_url.ends_with("/v1") {
            format!("{}/models", base_url)
        } else {
            format!("{}/v1/models", base_url)
        };

        match self
            .http_client
            .get(&models_url)
            .timeout(Duration::from_secs(5))
            .send()
            .await
        {
            Ok(resp) => resp.status().is_success(),
            Err(_) => false,
        }
    }

    /// Get the minimum health check interval across all providers
    pub fn min_health_check_interval(&self) -> Duration {
        self.providers
            .read()
            .iter()
            .map(|p| p.health_check_interval)
            .min()
            .unwrap_or(Duration::from_secs(10))
    }
}
