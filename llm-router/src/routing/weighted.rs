use super::{RouteRequest, RoutingStrategy};
use crate::provider::ProviderState;
use rand::Rng;

/// Weighted random selection based on provider weights.
/// Providers with higher weights receive proportionally more traffic.
pub struct Weighted;

impl Weighted {
    pub fn new() -> Self {
        Self
    }
}

impl RoutingStrategy for Weighted {
    fn name(&self) -> &str {
        "weighted"
    }

    fn select(&self, providers: &[ProviderState], _request: &RouteRequest) -> Option<usize> {
        if providers.is_empty() {
            return None;
        }

        let total_weight: u32 = providers.iter().map(|p| p.weight).sum();
        if total_weight == 0 {
            // Fallback to uniform random if all weights are zero
            let idx = rand::thread_rng().gen_range(0..providers.len());
            return Some(idx);
        }

        let mut rng = rand::thread_rng();
        let mut pick = rng.gen_range(0..total_weight);

        for (idx, provider) in providers.iter().enumerate() {
            if pick < provider.weight {
                return Some(idx);
            }
            pick -= provider.weight;
        }

        // Should not reach here, but fallback to last provider
        Some(providers.len() - 1)
    }

    fn on_complete(&self, _provider_idx: usize, _latency_ms: u64, _success: bool) {
        // Weighted routing doesn't use completion feedback
    }
}
