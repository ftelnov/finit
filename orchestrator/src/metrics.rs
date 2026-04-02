use axum::http::StatusCode;
use axum::response::IntoResponse;
use prometheus::{
    Encoder, HistogramOpts, HistogramVec, IntCounterVec, IntGauge, Opts, Registry, TextEncoder,
};
use std::sync::Arc;

/// Prometheus metrics for the orchestrator.
#[derive(Clone)]
pub struct Metrics {
    pub registry: Arc<Registry>,
    pub tasks_created: IntCounterVec,
    pub tasks_completed: IntCounterVec,
    pub tasks_failed: IntCounterVec,
    pub tasks_active: IntGauge,
    pub agent_calls: IntCounterVec,
    pub agent_call_duration: HistogramVec,
    pub agent_call_errors: IntCounterVec,
    pub sse_connections: IntGauge,
    pub http_requests: IntCounterVec,
    pub http_request_duration: HistogramVec,
    pub supervisor_iterations: IntCounterVec,
}

impl Metrics {
    pub fn new() -> Self {
        let registry = Registry::new();

        let tasks_created = IntCounterVec::new(
            Opts::new("finit_tasks_created_total", "Total tasks created"),
            &["project_id"],
        )
        .expect("metric creation failed");

        let tasks_completed = IntCounterVec::new(
            Opts::new("finit_tasks_completed_total", "Total tasks completed"),
            &["status"],
        )
        .expect("metric creation failed");

        let tasks_failed = IntCounterVec::new(
            Opts::new("finit_tasks_failed_total", "Total tasks failed"),
            &["reason"],
        )
        .expect("metric creation failed");

        let tasks_active = IntGauge::new("finit_tasks_active", "Currently active tasks")
            .expect("metric creation failed");

        let agent_calls = IntCounterVec::new(
            Opts::new("finit_agent_calls_total", "Total A2A calls to agents"),
            &["agent_id", "method"],
        )
        .expect("metric creation failed");

        let agent_call_duration = HistogramVec::new(
            HistogramOpts::new(
                "finit_agent_call_duration_seconds",
                "Duration of A2A calls in seconds",
            )
            .buckets(vec![0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0]),
            &["agent_id"],
        )
        .expect("metric creation failed");

        let agent_call_errors = IntCounterVec::new(
            Opts::new(
                "finit_agent_call_errors_total",
                "Total A2A call errors",
            ),
            &["agent_id", "error_type"],
        )
        .expect("metric creation failed");

        let sse_connections =
            IntGauge::new("finit_sse_connections", "Active SSE connections")
                .expect("metric creation failed");

        let http_requests = IntCounterVec::new(
            Opts::new("finit_http_requests_total", "Total HTTP requests"),
            &["method", "path", "status"],
        )
        .expect("metric creation failed");

        let http_request_duration = HistogramVec::new(
            HistogramOpts::new(
                "finit_http_request_duration_seconds",
                "HTTP request duration in seconds",
            )
            .buckets(vec![0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0]),
            &["method", "path"],
        )
        .expect("metric creation failed");

        let supervisor_iterations = IntCounterVec::new(
            Opts::new(
                "finit_supervisor_iterations_total",
                "Total supervisor loop iterations",
            ),
            &["task_id", "action"],
        )
        .expect("metric creation failed");

        registry.register(Box::new(tasks_created.clone())).ok();
        registry.register(Box::new(tasks_completed.clone())).ok();
        registry.register(Box::new(tasks_failed.clone())).ok();
        registry.register(Box::new(tasks_active.clone())).ok();
        registry.register(Box::new(agent_calls.clone())).ok();
        registry.register(Box::new(agent_call_duration.clone())).ok();
        registry.register(Box::new(agent_call_errors.clone())).ok();
        registry.register(Box::new(sse_connections.clone())).ok();
        registry.register(Box::new(http_requests.clone())).ok();
        registry
            .register(Box::new(http_request_duration.clone()))
            .ok();
        registry
            .register(Box::new(supervisor_iterations.clone()))
            .ok();

        Self {
            registry: Arc::new(registry),
            tasks_created,
            tasks_completed,
            tasks_failed,
            tasks_active,
            agent_calls,
            agent_call_duration,
            agent_call_errors,
            sse_connections,
            http_requests,
            http_request_duration,
            supervisor_iterations,
        }
    }
}

/// Handler for GET /metrics - returns Prometheus text format.
pub async fn metrics_handler(
    axum::extract::State(state): axum::extract::State<crate::AppState>,
) -> impl IntoResponse {
    let encoder = TextEncoder::new();
    let metric_families = state.metrics.registry.gather();
    let mut buffer = Vec::new();
    match encoder.encode(&metric_families, &mut buffer) {
        Ok(()) => (
            StatusCode::OK,
            [(
                axum::http::header::CONTENT_TYPE,
                encoder.format_type().to_string(),
            )],
            buffer,
        )
            .into_response(),
        Err(e) => {
            tracing::error!("failed to encode metrics: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}
