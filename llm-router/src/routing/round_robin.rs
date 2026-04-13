use super::{RouteRequest, RoutingStrategy};
use crate::provider::ProviderState;
use std::sync::atomic::{AtomicUsize, Ordering};

/// Simple round-robin routing strategy.
/// Cycles through providers sequentially.
pub struct RoundRobin {
    counter: AtomicUsize,
}

impl RoundRobin {
    pub fn new() -> Self {
        Self {
            counter: AtomicUsize::new(0),
        }
    }
}

impl RoutingStrategy for RoundRobin {
    fn name(&self) -> &str {
        "round-robin"
    }

    fn select(&self, providers: &[ProviderState], _request: &RouteRequest) -> Option<usize> {
        if providers.is_empty() {
            return None;
        }
        let idx = self.counter.fetch_add(1, Ordering::Relaxed);
        Some(idx % providers.len())
    }

    fn on_complete(&self, _provider_idx: usize, _latency_ms: u64, _success: bool) {
        // Round-robin doesn't use completion feedback
    }
}
