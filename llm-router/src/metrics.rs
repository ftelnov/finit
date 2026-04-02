use prometheus::{
    register_counter_vec, register_gauge_vec, register_histogram_vec, CounterVec, GaugeVec,
    HistogramVec,
};
use std::sync::LazyLock;

/// Total number of LLM requests
pub static LLM_REQUESTS_TOTAL: LazyLock<CounterVec> = LazyLock::new(|| {
    register_counter_vec!(
        "finit_llm_requests_total",
        "Total number of LLM requests",
        &["provider", "model", "status"]
    )
    .expect("failed to register finit_llm_requests_total")
});

/// Request duration in seconds
pub static LLM_REQUEST_DURATION: LazyLock<HistogramVec> = LazyLock::new(|| {
    register_histogram_vec!(
        "finit_llm_request_duration_seconds",
        "LLM request duration in seconds",
        &["provider", "model"],
        vec![0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]
    )
    .expect("failed to register finit_llm_request_duration_seconds")
});

/// Total tokens processed
pub static LLM_TOKENS_TOTAL: LazyLock<CounterVec> = LazyLock::new(|| {
    register_counter_vec!(
        "finit_llm_tokens_total",
        "Total tokens processed",
        &["provider", "model", "direction"]
    )
    .expect("failed to register finit_llm_tokens_total")
});

/// Provider health status (1=healthy, 0=unhealthy)
pub static LLM_PROVIDER_HEALTH: LazyLock<GaugeVec> = LazyLock::new(|| {
    register_gauge_vec!(
        "finit_llm_provider_health",
        "Provider health status (1=healthy, 0=unhealthy)",
        &["provider"]
    )
    .expect("failed to register finit_llm_provider_health")
});

/// Active in-flight requests per provider
pub static LLM_ACTIVE_REQUESTS: LazyLock<GaugeVec> = LazyLock::new(|| {
    register_gauge_vec!(
        "finit_llm_active_requests",
        "Active in-flight requests per provider",
        &["provider"]
    )
    .expect("failed to register finit_llm_active_requests")
});

/// Circuit breaker state per provider (0=closed, 1=open, 0.5=half-open)
pub static LLM_CIRCUIT_BREAKER_STATE: LazyLock<GaugeVec> = LazyLock::new(|| {
    register_gauge_vec!(
        "finit_llm_circuit_breaker_state",
        "Circuit breaker state (0=closed, 1=open, 0.5=half-open)",
        &["provider"]
    )
    .expect("failed to register finit_llm_circuit_breaker_state")
});

/// EWMA latency per provider in seconds
pub static LLM_PROVIDER_LATENCY_EWMA: LazyLock<GaugeVec> = LazyLock::new(|| {
    register_gauge_vec!(
        "finit_llm_provider_latency_ewma_seconds",
        "EWMA latency per provider in seconds",
        &["provider"]
    )
    .expect("failed to register finit_llm_provider_latency_ewma_seconds")
});

/// Initialize all metrics (force lazy registration)
pub fn init_metrics() {
    LazyLock::force(&LLM_REQUESTS_TOTAL);
    LazyLock::force(&LLM_REQUEST_DURATION);
    LazyLock::force(&LLM_TOKENS_TOTAL);
    LazyLock::force(&LLM_PROVIDER_HEALTH);
    LazyLock::force(&LLM_ACTIVE_REQUESTS);
    LazyLock::force(&LLM_CIRCUIT_BREAKER_STATE);
    LazyLock::force(&LLM_PROVIDER_LATENCY_EWMA);
}
