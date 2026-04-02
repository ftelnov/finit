use super::{RouteRequest, RoutingStrategy};
use crate::provider::ProviderState;

/// Least-load routing strategy (inspired by sglang's shortest-queue).
/// Selects the provider with the fewest outstanding requests.
/// When there are ties, selects the provider with the lowest EWMA latency.
pub struct LeastLoad;

impl LeastLoad {
    pub fn new() -> Self {
        Self
    }
}

impl RoutingStrategy for LeastLoad {
    fn name(&self) -> &str {
        "least-load"
    }

    fn select(&self, providers: &[ProviderState], _request: &RouteRequest) -> Option<usize> {
        if providers.is_empty() {
            return None;
        }

        let mut best_idx = 0;
        let mut best_active = providers[0].active_requests.load(std::sync::atomic::Ordering::Relaxed);
        let mut best_latency = providers[0].ewma_latency_ms();

        for (idx, provider) in providers.iter().enumerate().skip(1) {
            let active = provider.active_requests.load(std::sync::atomic::Ordering::Relaxed);
            let latency = provider.ewma_latency_ms();

            // Prefer fewer active requests; break ties with lower EWMA latency
            if active < best_active || (active == best_active && latency < best_latency) {
                best_idx = idx;
                best_active = active;
                best_latency = latency;
            }
        }

        Some(best_idx)
    }

    fn on_complete(&self, _provider_idx: usize, _latency_ms: u64, _success: bool) {
        // State updates happen in ProviderPool, not here
    }
}
