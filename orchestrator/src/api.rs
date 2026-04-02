use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::response::{IntoResponse, Response};
use axum::Json;
use futures_util::stream::Stream;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::convert::Infallible;
use std::pin::Pin;
use std::task::{Context, Poll};
use std::time::Duration;
use tokio_stream::wrappers::BroadcastStream;
use tokio_stream::StreamExt;
use uuid::Uuid;

use crate::a2a::A2AClient;
use crate::db;
use crate::metrics::Metrics;
use crate::supervisor;
use crate::AppState;

// ─── Error type ──────────────────────────────────────────────────────────────

#[derive(Debug)]
pub struct ApiError {
    status: StatusCode,
    message: String,
}

impl ApiError {
    pub fn not_found(msg: impl Into<String>) -> Self {
        Self {
            status: StatusCode::NOT_FOUND,
            message: msg.into(),
        }
    }

    pub fn bad_request(msg: impl Into<String>) -> Self {
        Self {
            status: StatusCode::BAD_REQUEST,
            message: msg.into(),
        }
    }

    pub fn internal(msg: impl Into<String>) -> Self {
        Self {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            message: msg.into(),
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let body = serde_json::json!({
            "error": {
                "code": self.status.as_u16(),
                "message": self.message,
            }
        });
        (self.status, Json(body)).into_response()
    }
}

impl From<sqlx::Error> for ApiError {
    fn from(e: sqlx::Error) -> Self {
        tracing::error!("database error: {}", e);
        Self::internal("database error")
    }
}

impl From<anyhow::Error> for ApiError {
    fn from(e: anyhow::Error) -> Self {
        tracing::error!("internal error: {}", e);
        Self::internal(e.to_string())
    }
}

// ─── Request/Response types ──────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
pub struct CreateTaskRequest {
    pub input: String,
    pub project_id: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct TaskResponse {
    pub id: String,
    pub project_id: Option<String>,
    pub input: String,
    pub status: String,
    pub workspace_id: Option<String>,
    pub iteration: i32,
    pub error: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    pub completed_at: Option<String>,
}

impl From<db::Task> for TaskResponse {
    fn from(t: db::Task) -> Self {
        Self {
            id: t.id,
            project_id: t.project_id,
            input: t.input,
            status: t.status,
            workspace_id: t.workspace_id,
            iteration: t.iteration,
            error: t.error,
            created_at: t.created_at.to_rfc3339(),
            updated_at: t.updated_at.to_rfc3339(),
            completed_at: t.completed_at.map(|dt| dt.to_rfc3339()),
        }
    }
}

#[derive(Debug, Deserialize)]
pub struct ListTasksQuery {
    pub status: Option<String>,
    pub limit: Option<i64>,
    pub offset: Option<i64>,
}

#[derive(Debug, Deserialize)]
pub struct UserInputRequest {
    /// "approve" or "reject" for spec approval; free text for questions.
    pub action: String,
    /// Optional additional data for the response (e.g., user's answer).
    #[serde(default)]
    #[allow(dead_code)]
    pub data: Option<Value>,
}

#[derive(Debug, Deserialize)]
pub struct RegisterAgentRequest {
    pub url: String,
}

#[derive(Debug, Serialize)]
pub struct AgentResponse {
    pub id: String,
    pub name: String,
    pub url: String,
    pub status: String,
    pub agent_card: Value,
    pub last_health_check: Option<String>,
    pub registered_at: Option<String>,
}

impl From<db::Agent> for AgentResponse {
    fn from(a: db::Agent) -> Self {
        Self {
            id: a.id,
            name: a.name,
            url: a.url,
            status: a.status,
            agent_card: a.agent_card,
            last_health_check: a.last_health_check.map(|dt| dt.to_rfc3339()),
            registered_at: a.registered_at.map(|dt| dt.to_rfc3339()),
        }
    }
}

#[derive(Debug, Serialize)]
pub struct HealthResponse {
    pub status: String,
    pub database: String,
}

// ─── Task handlers ───────────────────────────────────────────────────────────

/// POST /api/tasks - Create a new task and start the supervisor loop.
pub async fn create_task(
    State(state): State<AppState>,
    Json(body): Json<CreateTaskRequest>,
) -> Result<(StatusCode, Json<TaskResponse>), ApiError> {
    if body.input.trim().is_empty() {
        return Err(ApiError::bad_request("input must not be empty"));
    }

    let task_id = format!("task-{}", Uuid::new_v4());

    let task = db::create_task(
        &state.pool,
        &task_id,
        &body.input,
        body.project_id.as_deref(),
    )
    .await?;

    // Create budget for the task
    db::create_task_budget(
        &state.pool,
        &task_id,
        state.config.default_max_tokens,
        state.config.default_max_calls,
        state.config.max_iterations,
        state.config.max_task_duration_s,
    )
    .await?;

    // Initialize event sequence
    state.event_bus.load_sequence(&task_id).await.ok();

    state
        .metrics
        .tasks_created
        .with_label_values(&[body.project_id.as_deref().unwrap_or("none")])
        .inc();

    // Spawn supervisor loop
    let pool = state.pool.clone();
    let config = state.config.clone();
    let a2a_client = state.a2a_client.clone();
    let event_bus = state.event_bus.clone();
    let metrics = state.metrics.clone();
    let tid = task_id.clone();

    tokio::spawn(async move {
        supervisor::run(pool, config, a2a_client, event_bus, metrics, tid).await;
    });

    Ok((StatusCode::CREATED, Json(TaskResponse::from(task))))
}

/// GET /api/tasks - List tasks with optional filters.
pub async fn list_tasks(
    State(state): State<AppState>,
    Query(query): Query<ListTasksQuery>,
) -> Result<Json<Vec<TaskResponse>>, ApiError> {
    let limit = query.limit.unwrap_or(50).min(100);
    let offset = query.offset.unwrap_or(0).max(0);

    let tasks = db::list_tasks(&state.pool, query.status.as_deref(), limit, offset).await?;
    let response: Vec<TaskResponse> = tasks.into_iter().map(TaskResponse::from).collect();
    Ok(Json(response))
}

/// GET /api/tasks/{id} - Get a specific task.
pub async fn get_task(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<TaskResponse>, ApiError> {
    let task = db::get_task(&state.pool, &id)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("task {} not found", id)))?;
    Ok(Json(TaskResponse::from(task)))
}

/// DELETE /api/tasks/{id} - Cancel a task.
pub async fn cancel_task(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<TaskResponse>, ApiError> {
    let task = db::get_task(&state.pool, &id)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("task {} not found", id)))?;

    if task.status == "completed" || task.status == "cancelled" || task.status == "failed" {
        return Err(ApiError::bad_request(format!(
            "cannot cancel task in '{}' state",
            task.status
        )));
    }

    let updated = db::update_task_status(&state.pool, &id, "cancelled", Some("cancelled by user"))
        .await?;

    state
        .event_bus
        .emit_run_error(&id, "cancelled by user", None)
        .await
        .ok();

    Ok(Json(TaskResponse::from(updated)))
}

/// POST /api/tasks/{id}/input - Provide user input (e.g., approve/reject spec).
pub async fn task_input(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UserInputRequest>,
) -> Result<Json<TaskResponse>, ApiError> {
    let task = db::get_task(&state.pool, &id)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("task {} not found", id)))?;

    if task.status != "awaiting_input" {
        return Err(ApiError::bad_request(format!(
            "task is not awaiting input (current status: {})",
            task.status
        )));
    }

    match body.action.as_str() {
        "approve" => {
            db::update_task_spec_status(&state.pool, &id, "approved").await?;
            let updated = db::update_task_status(&state.pool, &id, "running", None).await?;
            Ok(Json(TaskResponse::from(updated)))
        }
        "reject" => {
            db::update_task_spec_status(&state.pool, &id, "rejected").await?;
            let updated = db::update_task_status(
                &state.pool,
                &id,
                "cancelled",
                Some("spec rejected by user"),
            )
            .await?;
            Ok(Json(TaskResponse::from(updated)))
        }
        "respond" => {
            // Generic user response (for worker questions, etc.)
            // Resume the task
            let updated = db::update_task_status(&state.pool, &id, "running", None).await?;
            Ok(Json(TaskResponse::from(updated)))
        }
        _ => Err(ApiError::bad_request(format!(
            "unknown action '{}'. Expected: approve, reject, respond",
            body.action
        ))),
    }
}

/// Drop guard that decrements the SSE connection counter when the stream is dropped.
struct SseDropGuard {
    metrics: Metrics,
}

impl Drop for SseDropGuard {
    fn drop(&mut self) {
        self.metrics.sse_connections.dec();
    }
}

/// A stream wrapper that holds a drop guard for SSE connection tracking.
struct SseStream {
    inner: Pin<Box<dyn Stream<Item = Result<Event, Infallible>> + Send>>,
    _guard: SseDropGuard,
}

impl Stream for SseStream {
    type Item = Result<Event, Infallible>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.inner.as_mut().poll_next(cx)
    }
}

/// GET /ag-ui/tasks/{id}/events - SSE stream of AG-UI events.
pub async fn task_events_sse(
    State(state): State<AppState>,
    Path(id): Path<String>,
    headers: axum::http::HeaderMap,
) -> Result<Sse<impl Stream<Item = Result<Event, Infallible>>>, ApiError> {
    // Verify task exists
    let _task = db::get_task(&state.pool, &id)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("task {} not found", id)))?;

    state.metrics.sse_connections.inc();
    let metrics = state.metrics.clone();

    // Check Last-Event-ID for replay
    let last_event_id: i32 = headers
        .get("Last-Event-ID")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.parse().ok())
        .unwrap_or(0);

    // Get replay events
    let replay_events = state
        .event_bus
        .replay_events(&id, last_event_id)
        .await
        .unwrap_or_default();

    // Subscribe to live events
    let rx = state.event_bus.subscribe(&id).await;

    let task_id = id.clone();

    // Create the stream: replay historical events, then stream live ones
    let replay_stream = tokio_stream::iter(replay_events.into_iter().map(move |ev| {
        let data = serde_json::to_string(&ev.event).unwrap_or_default();
        Ok::<_, Infallible>(
            Event::default()
                .event(ev.event_type)
                .data(data)
                .id(ev.seq.to_string()),
        )
    }));

    let live_stream = BroadcastStream::new(rx).filter_map(move |result| {
        match result {
            Ok(ev) if ev.task_id == task_id => {
                let data = serde_json::to_string(&ev.event).unwrap_or_default();
                Some(Ok(
                    Event::default()
                        .event(ev.event_type)
                        .data(data)
                        .id(ev.seq.to_string()),
                ))
            }
            _ => None,
        }
    });

    // Combine replay and live streams
    let combined = replay_stream.chain(live_stream);

    // Wrap in a struct that decrements SSE connections on drop
    let guard = SseDropGuard {
        metrics: metrics.clone(),
    };

    let tracked_stream = SseStream {
        inner: Box::pin(combined),
        _guard: guard,
    };

    Ok(Sse::new(tracked_stream).keep_alive(
        KeepAlive::new()
            .interval(Duration::from_secs(15))
            .text("ping"),
    ))
}

// ─── Agent handlers ──────────────────────────────────────────────────────────

/// POST /api/agents - Register a new agent by fetching its Agent Card.
pub async fn register_agent(
    State(state): State<AppState>,
    Json(body): Json<RegisterAgentRequest>,
) -> Result<(StatusCode, Json<AgentResponse>), ApiError> {
    if body.url.trim().is_empty() {
        return Err(ApiError::bad_request("url must not be empty"));
    }

    let a2a_client = A2AClient::new(Duration::from_secs(10));

    // Fetch agent card
    let card = a2a_client
        .discover(&body.url)
        .await
        .map_err(|e| ApiError::bad_request(format!("failed to discover agent: {}", e)))?;

    let agent_id = card.name.clone();
    let card_json = serde_json::to_value(&card)
        .map_err(|e| ApiError::internal(format!("failed to serialize agent card: {}", e)))?;

    // Generate a simple JWT token (for PoC, use a UUID-based token)
    let jwt_token = format!("finit-agent-{}-{}", agent_id, Uuid::new_v4());

    let agent = db::create_agent(
        &state.pool,
        &agent_id,
        &card.name,
        &body.url,
        &card_json,
        &jwt_token,
    )
    .await?;

    Ok((StatusCode::CREATED, Json(AgentResponse::from(agent))))
}

/// GET /api/agents - List all registered agents.
pub async fn list_agents(
    State(state): State<AppState>,
) -> Result<Json<Vec<AgentResponse>>, ApiError> {
    let agents = db::list_agents(&state.pool).await?;
    let response: Vec<AgentResponse> = agents.into_iter().map(AgentResponse::from).collect();
    Ok(Json(response))
}

/// GET /api/agents/{id} - Get a specific agent.
pub async fn get_agent(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<AgentResponse>, ApiError> {
    let agent = db::get_agent(&state.pool, &id)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("agent {} not found", id)))?;
    Ok(Json(AgentResponse::from(agent)))
}

/// DELETE /api/agents/{id} - Remove an agent.
pub async fn delete_agent(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    let deleted = db::delete_agent(&state.pool, &id).await?;
    if deleted {
        Ok(StatusCode::NO_CONTENT)
    } else {
        Err(ApiError::not_found(format!("agent {} not found", id)))
    }
}

// ─── Health check ────────────────────────────────────────────────────────────

/// GET /health - Health check endpoint.
pub async fn health_check(
    State(state): State<AppState>,
) -> Result<Json<HealthResponse>, ApiError> {
    let db_healthy = db::check_health(&state.pool).await.unwrap_or(false);
    let db_status = if db_healthy { "healthy" } else { "unhealthy" };
    let overall = if db_healthy { "healthy" } else { "unhealthy" };

    let response = HealthResponse {
        status: overall.to_string(),
        database: db_status.to_string(),
    };

    if db_healthy {
        Ok(Json(response))
    } else {
        Err(ApiError {
            status: StatusCode::SERVICE_UNAVAILABLE,
            message: "database unhealthy".to_string(),
        })
    }
}
