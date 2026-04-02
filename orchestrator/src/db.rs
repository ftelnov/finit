use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::PgPool;

// ─── Models ──────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct Task {
    pub id: String,
    pub project_id: Option<String>,
    pub input: String,
    pub status: String,
    pub workspace_id: Option<String>,
    pub iteration: i32,
    pub error: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct TaskSpec {
    pub id: i32,
    pub task_id: String,
    pub title: String,
    pub description: String,
    pub acceptance_criteria: Vec<String>,
    pub test_plan: serde_json::Value,
    pub files_affected: Option<Vec<String>>,
    pub domains: Option<Vec<String>>,
    pub status: String,
    pub version: i32,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, sqlx::FromRow)]
pub struct TaskBudget {
    pub task_id: String,
    pub max_tokens: i32,
    pub max_calls: i32,
    pub max_iterations: i32,
    pub max_duration_s: i32,
    pub spent_tokens: i32,
    pub spent_calls: i32,
    pub spent_cost: sqlx::types::BigDecimal,
    pub started_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct TaskArtifact {
    pub id: i32,
    pub task_id: String,
    pub iteration: i32,
    pub artifact_type: String,
    pub path: Option<String>,
    pub files_changed: Option<Vec<String>>,
    pub content: Option<String>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct TaskReview {
    pub id: i32,
    pub task_id: String,
    pub iteration: i32,
    pub verdict: String,
    pub findings: Option<serde_json::Value>,
    pub summary: Option<String>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct SupervisorDecision {
    pub id: i32,
    pub task_id: String,
    pub action: String,
    pub agent_id: Option<String>,
    pub reasoning: Option<String>,
    pub result_status: Option<String>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct TaskEvent {
    pub id: i64,
    pub task_id: String,
    pub seq: i32,
    pub event_type: String,
    pub event_data: serde_json::Value,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct Agent {
    pub id: String,
    pub name: String,
    pub url: String,
    pub agent_card: serde_json::Value,
    pub jwt_token: String,
    pub status: String,
    pub last_health_check: Option<DateTime<Utc>>,
    pub registered_at: Option<DateTime<Utc>>,
}

// ─── Task operations ─────────────────────────────────────────────────────────

pub async fn create_task(
    pool: &PgPool,
    id: &str,
    input: &str,
    project_id: Option<&str>,
) -> Result<Task, sqlx::Error> {
    sqlx::query_as::<_, Task>(
        r#"INSERT INTO tasks (id, project_id, input, status)
           VALUES ($1, $2, $3, 'created')
           RETURNING *"#,
    )
    .bind(id)
    .bind(project_id)
    .bind(input)
    .fetch_one(pool)
    .await
}

pub async fn get_task(pool: &PgPool, id: &str) -> Result<Option<Task>, sqlx::Error> {
    sqlx::query_as::<_, Task>("SELECT * FROM tasks WHERE id = $1")
        .bind(id)
        .fetch_optional(pool)
        .await
}

pub async fn list_tasks(
    pool: &PgPool,
    status: Option<&str>,
    limit: i64,
    offset: i64,
) -> Result<Vec<Task>, sqlx::Error> {
    if let Some(status) = status {
        sqlx::query_as::<_, Task>(
            "SELECT * FROM tasks WHERE status = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
        )
        .bind(status)
        .bind(limit)
        .bind(offset)
        .fetch_all(pool)
        .await
    } else {
        sqlx::query_as::<_, Task>(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT $1 OFFSET $2",
        )
        .bind(limit)
        .bind(offset)
        .fetch_all(pool)
        .await
    }
}

pub async fn update_task_status(
    pool: &PgPool,
    id: &str,
    status: &str,
    error: Option<&str>,
) -> Result<Task, sqlx::Error> {
    let now = Utc::now();
    let completed_at = if status == "completed" || status == "failed" || status == "cancelled" || status == "escalated" {
        Some(now)
    } else {
        None
    };
    sqlx::query_as::<_, Task>(
        r#"UPDATE tasks SET status = $2, error = $3, updated_at = $4, completed_at = COALESCE($5, completed_at)
           WHERE id = $1 RETURNING *"#,
    )
    .bind(id)
    .bind(status)
    .bind(error)
    .bind(now)
    .bind(completed_at)
    .fetch_one(pool)
    .await
}

pub async fn update_task_iteration(
    pool: &PgPool,
    id: &str,
    iteration: i32,
) -> Result<(), sqlx::Error> {
    sqlx::query("UPDATE tasks SET iteration = $2, updated_at = NOW() WHERE id = $1")
        .bind(id)
        .bind(iteration)
        .execute(pool)
        .await?;
    Ok(())
}

pub async fn update_task_workspace(
    pool: &PgPool,
    task_id: &str,
    workspace_id: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query("UPDATE tasks SET workspace_id = $2, updated_at = NOW() WHERE id = $1")
        .bind(task_id)
        .bind(workspace_id)
        .execute(pool)
        .await?;
    Ok(())
}

// ─── Task budget operations ──────────────────────────────────────────────────

pub async fn create_task_budget(
    pool: &PgPool,
    task_id: &str,
    max_tokens: i32,
    max_calls: i32,
    max_iterations: i32,
    max_duration_s: i32,
) -> Result<TaskBudget, sqlx::Error> {
    sqlx::query_as::<_, TaskBudget>(
        r#"INSERT INTO task_budgets (task_id, max_tokens, max_calls, max_iterations, max_duration_s, started_at)
           VALUES ($1, $2, $3, $4, $5, NOW())
           RETURNING *"#,
    )
    .bind(task_id)
    .bind(max_tokens)
    .bind(max_calls)
    .bind(max_iterations)
    .bind(max_duration_s)
    .fetch_one(pool)
    .await
}

pub async fn get_task_budget(
    pool: &PgPool,
    task_id: &str,
) -> Result<Option<TaskBudget>, sqlx::Error> {
    sqlx::query_as::<_, TaskBudget>("SELECT * FROM task_budgets WHERE task_id = $1")
        .bind(task_id)
        .fetch_optional(pool)
        .await
}

// ─── Task spec operations ────────────────────────────────────────────────────

pub async fn create_task_spec(
    pool: &PgPool,
    task_id: &str,
    title: &str,
    description: &str,
    acceptance_criteria: &[String],
    test_plan: &serde_json::Value,
    files_affected: Option<&[String]>,
    domains: Option<&[String]>,
) -> Result<TaskSpec, sqlx::Error> {
    sqlx::query_as::<_, TaskSpec>(
        r#"INSERT INTO task_specs (task_id, title, description, acceptance_criteria, test_plan, files_affected, domains)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           RETURNING *"#,
    )
    .bind(task_id)
    .bind(title)
    .bind(description)
    .bind(acceptance_criteria)
    .bind(test_plan)
    .bind(files_affected)
    .bind(domains)
    .fetch_one(pool)
    .await
}

pub async fn get_task_spec(
    pool: &PgPool,
    task_id: &str,
) -> Result<Option<TaskSpec>, sqlx::Error> {
    sqlx::query_as::<_, TaskSpec>(
        "SELECT * FROM task_specs WHERE task_id = $1 ORDER BY version DESC LIMIT 1",
    )
    .bind(task_id)
    .fetch_optional(pool)
    .await
}

pub async fn update_task_spec_status(
    pool: &PgPool,
    task_id: &str,
    status: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        "UPDATE task_specs SET status = $2 WHERE task_id = $1 AND version = (SELECT MAX(version) FROM task_specs WHERE task_id = $1)",
    )
    .bind(task_id)
    .bind(status)
    .execute(pool)
    .await?;
    Ok(())
}

// ─── Task artifact operations ────────────────────────────────────────────────

pub async fn create_task_artifact(
    pool: &PgPool,
    task_id: &str,
    iteration: i32,
    artifact_type: &str,
    path: Option<&str>,
    files_changed: Option<&[String]>,
    content: Option<&str>,
) -> Result<TaskArtifact, sqlx::Error> {
    sqlx::query_as::<_, TaskArtifact>(
        r#"INSERT INTO task_artifacts (task_id, iteration, artifact_type, path, files_changed, content)
           VALUES ($1, $2, $3, $4, $5, $6)
           RETURNING *"#,
    )
    .bind(task_id)
    .bind(iteration)
    .bind(artifact_type)
    .bind(path)
    .bind(files_changed)
    .bind(content)
    .fetch_one(pool)
    .await
}

pub async fn get_task_artifacts(
    pool: &PgPool,
    task_id: &str,
) -> Result<Vec<TaskArtifact>, sqlx::Error> {
    sqlx::query_as::<_, TaskArtifact>(
        "SELECT * FROM task_artifacts WHERE task_id = $1 ORDER BY created_at",
    )
    .bind(task_id)
    .fetch_all(pool)
    .await
}

// ─── Task review operations ──────────────────────────────────────────────────

pub async fn create_task_review(
    pool: &PgPool,
    task_id: &str,
    iteration: i32,
    verdict: &str,
    findings: Option<&serde_json::Value>,
    summary: Option<&str>,
) -> Result<TaskReview, sqlx::Error> {
    sqlx::query_as::<_, TaskReview>(
        r#"INSERT INTO task_reviews (task_id, iteration, verdict, findings, summary)
           VALUES ($1, $2, $3, $4, $5)
           RETURNING *"#,
    )
    .bind(task_id)
    .bind(iteration)
    .bind(verdict)
    .bind(findings)
    .bind(summary)
    .fetch_one(pool)
    .await
}

pub async fn get_latest_review(
    pool: &PgPool,
    task_id: &str,
) -> Result<Option<TaskReview>, sqlx::Error> {
    sqlx::query_as::<_, TaskReview>(
        "SELECT * FROM task_reviews WHERE task_id = $1 ORDER BY iteration DESC LIMIT 1",
    )
    .bind(task_id)
    .fetch_optional(pool)
    .await
}

// ─── Supervisor decisions ────────────────────────────────────────────────────

pub async fn create_supervisor_decision(
    pool: &PgPool,
    task_id: &str,
    action: &str,
    agent_id: Option<&str>,
    reasoning: Option<&str>,
    result_status: Option<&str>,
) -> Result<SupervisorDecision, sqlx::Error> {
    sqlx::query_as::<_, SupervisorDecision>(
        r#"INSERT INTO supervisor_decisions (task_id, action, agent_id, reasoning, result_status)
           VALUES ($1, $2, $3, $4, $5)
           RETURNING *"#,
    )
    .bind(task_id)
    .bind(action)
    .bind(agent_id)
    .bind(reasoning)
    .bind(result_status)
    .fetch_one(pool)
    .await
}

// ─── Task events ─────────────────────────────────────────────────────────────

pub async fn create_task_event(
    pool: &PgPool,
    task_id: &str,
    seq: i32,
    event_type: &str,
    event_data: &serde_json::Value,
) -> Result<TaskEvent, sqlx::Error> {
    sqlx::query_as::<_, TaskEvent>(
        r#"INSERT INTO task_events (task_id, seq, event_type, event_data)
           VALUES ($1, $2, $3, $4)
           RETURNING *"#,
    )
    .bind(task_id)
    .bind(seq)
    .bind(event_type)
    .bind(event_data)
    .fetch_one(pool)
    .await
}

pub async fn get_task_events_after(
    pool: &PgPool,
    task_id: &str,
    after_seq: i32,
) -> Result<Vec<TaskEvent>, sqlx::Error> {
    sqlx::query_as::<_, TaskEvent>(
        "SELECT * FROM task_events WHERE task_id = $1 AND seq > $2 ORDER BY seq",
    )
    .bind(task_id)
    .bind(after_seq)
    .fetch_all(pool)
    .await
}

pub async fn get_next_event_seq(pool: &PgPool, task_id: &str) -> Result<i32, sqlx::Error> {
    let row: Option<(Option<i32>,)> =
        sqlx::query_as("SELECT MAX(seq) FROM task_events WHERE task_id = $1")
            .bind(task_id)
            .fetch_optional(pool)
            .await?;
    Ok(row.and_then(|r| r.0).unwrap_or(0) + 1)
}

// ─── Agent operations ────────────────────────────────────────────────────────

pub async fn create_agent(
    pool: &PgPool,
    id: &str,
    name: &str,
    url: &str,
    agent_card: &serde_json::Value,
    jwt_token: &str,
) -> Result<Agent, sqlx::Error> {
    sqlx::query_as::<_, Agent>(
        r#"INSERT INTO agents (id, name, url, agent_card, jwt_token, status)
           VALUES ($1, $2, $3, $4, $5, 'healthy')
           ON CONFLICT (id) DO UPDATE SET
               name = EXCLUDED.name,
               url = EXCLUDED.url,
               agent_card = EXCLUDED.agent_card,
               jwt_token = EXCLUDED.jwt_token,
               status = 'healthy'
           RETURNING *"#,
    )
    .bind(id)
    .bind(name)
    .bind(url)
    .bind(agent_card)
    .bind(jwt_token)
    .fetch_one(pool)
    .await
}

pub async fn get_agent(pool: &PgPool, id: &str) -> Result<Option<Agent>, sqlx::Error> {
    sqlx::query_as::<_, Agent>("SELECT * FROM agents WHERE id = $1")
        .bind(id)
        .fetch_optional(pool)
        .await
}

pub async fn list_agents(pool: &PgPool) -> Result<Vec<Agent>, sqlx::Error> {
    sqlx::query_as::<_, Agent>("SELECT * FROM agents ORDER BY registered_at")
        .fetch_all(pool)
        .await
}

pub async fn delete_agent(pool: &PgPool, id: &str) -> Result<bool, sqlx::Error> {
    let result = sqlx::query("DELETE FROM agents WHERE id = $1")
        .bind(id)
        .execute(pool)
        .await?;
    Ok(result.rows_affected() > 0)
}

pub async fn update_agent_health(
    pool: &PgPool,
    id: &str,
    status: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query("UPDATE agents SET status = $2, last_health_check = NOW() WHERE id = $1")
        .bind(id)
        .bind(status)
        .execute(pool)
        .await?;
    Ok(())
}

pub async fn get_healthy_agents(pool: &PgPool) -> Result<Vec<Agent>, sqlx::Error> {
    sqlx::query_as::<_, Agent>("SELECT * FROM agents WHERE status = 'healthy' ORDER BY registered_at")
        .fetch_all(pool)
        .await
}

pub async fn get_agent_by_name(pool: &PgPool, name: &str) -> Result<Option<Agent>, sqlx::Error> {
    sqlx::query_as::<_, Agent>("SELECT * FROM agents WHERE name = $1 AND status = 'healthy'")
        .bind(name)
        .fetch_optional(pool)
        .await
}

// ─── Health check ────────────────────────────────────────────────────────────

pub async fn check_health(pool: &PgPool) -> Result<bool, sqlx::Error> {
    let row: (i32,) = sqlx::query_as("SELECT 1 as health")
        .fetch_one(pool)
        .await?;
    Ok(row.0 == 1)
}
