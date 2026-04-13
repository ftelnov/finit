use super::{RouteRequest, RoutingStrategy};
use crate::provider::ProviderState;
use parking_lot::Mutex;
use std::collections::HashMap;

/// Cache-aware routing strategy inspired by sglang's radix-tree prefix matching.
///
/// Hashes the system prompt (+ first N tokens) and routes to the provider that
/// previously handled similar prefixes, maximizing KV cache reuse.
///
/// Stores provider **name** (not index) to be robust across topology changes.
pub struct CacheAware {
    inner: Mutex<CacheAwareInner>,
}

struct CacheAwareInner {
    /// Maps prompt hash to (provider_name, last_access_order)
    cache: HashMap<u64, CacheEntry>,
    /// Monotonically increasing access counter for LRU
    access_counter: u64,
    /// Maximum number of entries before eviction
    max_entries: usize,
}

struct CacheEntry {
    provider_name: String,
    last_access: u64,
}

impl CacheAware {
    pub fn new(max_entries: usize) -> Self {
        Self {
            inner: Mutex::new(CacheAwareInner {
                cache: HashMap::new(),
                access_counter: 0,
                max_entries,
            }),
        }
    }

    /// Evict the least recently used entry if over capacity
    fn maybe_evict(inner: &mut CacheAwareInner) {
        if inner.cache.len() <= inner.max_entries {
            return;
        }

        let lru_key = inner
            .cache
            .iter()
            .min_by_key(|(_, entry)| entry.last_access)
            .map(|(k, _)| *k);

        if let Some(key) = lru_key {
            inner.cache.remove(&key);
        }
    }
}

impl RoutingStrategy for CacheAware {
    fn name(&self) -> &str {
        "cache-aware"
    }

    fn select(&self, providers: &[ProviderState], request: &RouteRequest) -> Option<usize> {
        if providers.is_empty() {
            return None;
        }

        let prompt_hash = match request.prompt_hash {
            Some(h) => h,
            None => {
                return select_least_load(providers);
            }
        };

        let mut inner = self.inner.lock();
        inner.access_counter += 1;
        let access = inner.access_counter;

        if let Some(entry) = inner.cache.get_mut(&prompt_hash) {
            entry.last_access = access;

            // Find the cached provider by name in the current candidate list
            if let Some(idx) = providers.iter().position(|p| p.name == entry.provider_name) {
                return Some(idx);
            }
            // Cached provider not in current candidates (unhealthy/removed), evict
            inner.cache.remove(&prompt_hash);
        }

        // No cache hit, select via least-load and record the mapping
        let selected = select_least_load(providers)?;

        inner.cache.insert(
            prompt_hash,
            CacheEntry {
                provider_name: providers[selected].name.clone(),
                last_access: access,
            },
        );
        Self::maybe_evict(&mut inner);

        Some(selected)
    }

    fn on_complete(&self, _provider_idx: usize, _latency_ms: u64, _success: bool) {
        // Cache-aware routing updates happen during select()
    }
}

/// Fallback: select provider with least active requests
fn select_least_load(providers: &[ProviderState]) -> Option<usize> {
    if providers.is_empty() {
        return None;
    }

    providers
        .iter()
        .enumerate()
        .min_by_key(|(_, p)| p.active_requests.load(std::sync::atomic::Ordering::Relaxed))
        .map(|(idx, _)| idx)
}
