pub mod round_robin;
pub mod weighted;
pub mod least_load;
pub mod cache_aware;

use crate::provider::ProviderState;

/// Information about the incoming request used for routing decisions
#[derive(Debug, Clone)]
pub struct RouteRequest {
    /// The model requested
    pub model: String,
    /// Hash of the system prompt (for cache-aware routing)
    pub prompt_hash: Option<u64>,
}

/// Trait for routing strategies.
///
/// Implementations select which provider to send a request to
/// from a slice of candidate providers.
pub trait RoutingStrategy: Send + Sync {
    /// Human-readable name of this strategy
    fn name(&self) -> &str;

    /// Select a provider index from the candidates.
    /// Returns None if no suitable provider is available.
    fn select(&self, providers: &[ProviderState], request: &RouteRequest) -> Option<usize>;

    /// Called after a request completes, allowing the strategy to update internal state.
    fn on_complete(&self, provider_idx: usize, latency_ms: u64, success: bool);
}

/// Create a routing strategy by name
pub fn create_strategy(name: &str) -> Box<dyn RoutingStrategy> {
    match name {
        "round-robin" => Box::new(round_robin::RoundRobin::new()),
        "weighted" => Box::new(weighted::Weighted::new()),
        "least-load" => Box::new(least_load::LeastLoad::new()),
        "cache-aware" => Box::new(cache_aware::CacheAware::new(10_000)),
        "latency-based" => Box::new(least_load::LeastLoad::new()), // latency-based uses LeastLoad with EWMA
        _ => {
            tracing::warn!("Unknown routing strategy '{}', falling back to round-robin", name);
            Box::new(round_robin::RoundRobin::new())
        }
    }
}
