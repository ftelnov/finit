use anyhow::{anyhow, Result};
use serde_json::Value;
use sqlx::PgPool;
use std::time::{Duration, Instant};
use tracing::{error, info, warn};

use crate::a2a::{self, A2AClient};
use crate::agui::EventBus;
use crate::config::Config;
use crate::db;
use crate::metrics::Metrics;

/// Phases in the deterministic PoC supervisor flow.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Phase {
    /// Call planner to produce a spec.
    Spec,
    /// Wait for user to approve the spec.
    AwaitSpecApproval,
    /// Call bootstrapper to prepare workspace.
    Bootstrap,
    /// Call worker to implement the task.
    Work,
    /// Call reviewer to check the result.
    Review,
    /// Task is done.
    Completed,
}

/// Run the supervisor loop for a task. This is spawned as a tokio task.
pub async fn run(
    pool: PgPool,
    config: Config,
    a2a_client: A2AClient,
    event_bus: EventBus,
    metrics: Metrics,
    task_id: String,
) {
    metrics.tasks_active.inc();
    let result = supervisor_loop(&pool, &config, &a2a_client, &event_bus, &metrics, &task_id).await;
    metrics.tasks_active.dec();

    match result {
        Ok(()) => {
            info!(task_id = %task_id, "supervisor completed successfully");
        }
        Err(e) => {
            error!(task_id = %task_id, error = %e, "supervisor failed");
            let _ = db::update_task_status(&pool, &task_id, "failed", Some(&e.to_string())).await;
            let _ = event_bus
                .emit_run_error(&task_id, &e.to_string(), None)
                .await;
            metrics
                .tasks_failed
                .with_label_values(&["supervisor_error"])
                .inc();
        }
    }

    // Clean up the broadcast channel
    event_bus.remove_channel(&task_id).await;
}

async fn supervisor_loop(
    pool: &PgPool,
    config: &Config,
    a2a_client: &A2AClient,
    event_bus: &EventBus,
    metrics: &Metrics,
    task_id: &str,
) -> Result<()> {
    // Transition task to running
    db::update_task_status(pool, task_id, "running", None).await?;
    event_bus.emit_run_started(task_id).await?;

    let task_start = Instant::now();
    let max_duration = Duration::from_secs(config.max_task_duration_s as u64);

    // Determine starting phase based on current task state
    let mut phase = determine_initial_phase(pool, task_id).await?;
    let mut iteration: i32 = 0;

    loop {
        // Check timeout
        if task_start.elapsed() > max_duration {
            db::update_task_status(pool, task_id, "failed", Some("task duration exceeded"))
                .await?;
            event_bus
                .emit_run_error(task_id, "task duration exceeded", None)
                .await?;
            metrics
                .tasks_failed
                .with_label_values(&["timeout"])
                .inc();
            return Ok(());
        }

        // Check budget
        if let Some(budget) = db::get_task_budget(pool, task_id).await? {
            if budget.spent_tokens >= budget.max_tokens {
                db::update_task_status(pool, task_id, "escalated", Some("token budget exhausted"))
                    .await?;
                event_bus
                    .emit_run_error(task_id, "token budget exhausted", None)
                    .await?;
                return Ok(());
            }
            if iteration >= budget.max_iterations {
                db::update_task_status(
                    pool,
                    task_id,
                    "escalated",
                    Some("iteration limit reached"),
                )
                .await?;
                event_bus
                    .emit_run_error(task_id, "iteration limit reached", None)
                    .await?;
                return Ok(());
            }
        }

        // Reload task state to check for cancellation
        let task = db::get_task(pool, task_id)
            .await?
            .ok_or_else(|| anyhow!("task {} not found", task_id))?;
        if task.status == "cancelled" {
            info!(task_id = %task_id, "task was cancelled");
            return Ok(());
        }

        match phase {
            Phase::Spec => {
                phase = run_spec_phase(pool, config, a2a_client, event_bus, metrics, task_id)
                    .await?;
            }
            Phase::AwaitSpecApproval => {
                // Transition to awaiting_input and wait for user response.
                // The actual wait happens via the POST /api/tasks/{id}/input endpoint,
                // which will update the spec status and task status.
                let spec = db::get_task_spec(pool, task_id).await?;
                if let Some(spec) = &spec {
                    if spec.status == "approved" {
                        phase = Phase::Bootstrap;
                        continue;
                    } else if spec.status == "rejected" {
                        db::update_task_status(
                            pool,
                            task_id,
                            "cancelled",
                            Some("spec rejected by user"),
                        )
                        .await?;
                        event_bus
                            .emit_run_error(task_id, "spec rejected by user", None)
                            .await?;
                        return Ok(());
                    }
                }

                // Still pending -- wait and poll
                db::update_task_status(pool, task_id, "awaiting_input", None).await?;

                let spec_value = spec
                    .map(|s| {
                        serde_json::json!({
                            "title": s.title,
                            "description": s.description,
                            "acceptance_criteria": s.acceptance_criteria,
                            "test_plan": s.test_plan,
                            "files_affected": s.files_affected,
                            "domains": s.domains,
                        })
                    });

                event_bus
                    .emit_awaiting_input(
                        task_id,
                        "spec_approval",
                        spec_value,
                        None,
                        Some(vec!["approve".to_string(), "reject".to_string()]),
                    )
                    .await?;

                // Poll for spec approval
                loop {
                    tokio::time::sleep(Duration::from_secs(2)).await;

                    // Check for cancellation
                    let task = db::get_task(pool, task_id)
                        .await?
                        .ok_or_else(|| anyhow!("task {} not found", task_id))?;
                    if task.status == "cancelled" {
                        return Ok(());
                    }

                    if let Some(spec) = db::get_task_spec(pool, task_id).await? {
                        if spec.status == "approved" {
                            db::update_task_status(pool, task_id, "running", None).await?;
                            phase = Phase::Bootstrap;
                            break;
                        } else if spec.status == "rejected" {
                            db::update_task_status(
                                pool,
                                task_id,
                                "cancelled",
                                Some("spec rejected by user"),
                            )
                            .await?;
                            event_bus
                                .emit_run_error(task_id, "spec rejected by user", None)
                                .await?;
                            return Ok(());
                        }
                    }

                    // Check timeout while waiting
                    if task_start.elapsed() > max_duration {
                        db::update_task_status(
                            pool,
                            task_id,
                            "failed",
                            Some("task duration exceeded while awaiting input"),
                        )
                        .await?;
                        return Ok(());
                    }
                }
            }
            Phase::Bootstrap => {
                phase =
                    run_bootstrap_phase(pool, config, a2a_client, event_bus, metrics, task_id)
                        .await?;
            }
            Phase::Work => {
                iteration += 1;
                db::update_task_iteration(pool, task_id, iteration).await?;
                phase =
                    run_work_phase(pool, config, a2a_client, event_bus, metrics, task_id).await?;
            }
            Phase::Review => {
                phase = run_review_phase(
                    pool,
                    config,
                    a2a_client,
                    event_bus,
                    metrics,
                    task_id,
                    iteration,
                )
                .await?;

                // If review sends us back to work, check iteration limit
                if phase == Phase::Work && iteration >= config.max_iterations {
                    db::update_task_status(
                        pool,
                        task_id,
                        "escalated",
                        Some("max iterations reached after review failures"),
                    )
                    .await?;
                    event_bus
                        .emit_run_error(
                            task_id,
                            "max iterations reached after review failures",
                            None,
                        )
                        .await?;
                    return Ok(());
                }
            }
            Phase::Completed => {
                db::update_task_status(pool, task_id, "completed", None).await?;
                let artifacts = db::get_task_artifacts(pool, task_id).await?;
                let artifact_values: Vec<Value> = artifacts
                    .iter()
                    .map(|a| {
                        serde_json::json!({
                            "type": a.artifact_type,
                            "path": a.path,
                            "files_changed": a.files_changed,
                        })
                    })
                    .collect();

                event_bus
                    .emit_run_finished(
                        task_id,
                        "completed",
                        if artifact_values.is_empty() {
                            None
                        } else {
                            Some(artifact_values)
                        },
                    )
                    .await?;

                metrics
                    .tasks_completed
                    .with_label_values(&["completed"])
                    .inc();
                info!(task_id = %task_id, iterations = iteration, "task completed successfully");
                return Ok(());
            }
        }
    }
}

/// Determine initial phase based on existing state in the database.
async fn determine_initial_phase(pool: &PgPool, task_id: &str) -> Result<Phase> {
    // Check if spec exists and its status
    if let Some(spec) = db::get_task_spec(pool, task_id).await? {
        if spec.status == "approved" {
            // Check if workspace is assigned
            let task = db::get_task(pool, task_id)
                .await?
                .ok_or_else(|| anyhow!("task not found"))?;
            if task.workspace_id.is_some() {
                // Check for review results
                if let Some(review) = db::get_latest_review(pool, task_id).await? {
                    if review.verdict == "PASS" {
                        return Ok(Phase::Completed);
                    } else {
                        return Ok(Phase::Work);
                    }
                }
                return Ok(Phase::Work);
            }
            return Ok(Phase::Bootstrap);
        } else if spec.status == "pending" {
            return Ok(Phase::AwaitSpecApproval);
        }
    }
    Ok(Phase::Spec)
}

/// Call the planner agent to produce a spec.
async fn run_spec_phase(
    pool: &PgPool,
    config: &Config,
    a2a_client: &A2AClient,
    event_bus: &EventBus,
    metrics: &Metrics,
    task_id: &str,
) -> Result<Phase> {
    let step_start = Instant::now();
    event_bus
        .emit_step_started(task_id, "spec", "planner")
        .await?;

    let task = db::get_task(pool, task_id)
        .await?
        .ok_or_else(|| anyhow!("task not found"))?;

    // Find planner agent
    let planner = find_agent(pool, "planner").await?;

    let message = format!(
        "Create a specification for this task.\n\nTask: {}\n\n\
         Respond with a JSON object containing:\n\
         - title: string\n\
         - description: string\n\
         - acceptance_criteria: string[]\n\
         - test_plan: object with unit_tests[] and commands[]\n\
         - files_likely_affected: string[]\n\
         - domains: string[]",
        task.input
    );

    let metadata = serde_json::json!({
        "task_id": task_id,
        "project_id": task.project_id,
    });

    let result = call_agent_with_retry(
        a2a_client,
        &planner.url,
        &planner.jwt_token,
        task_id,
        &message,
        Some(metadata),
        config.retry_max,
        config.retry_delay_s,
        metrics,
        &planner.name,
    )
    .await?;

    let duration_ms = step_start.elapsed().as_millis() as u64;

    db::create_supervisor_decision(
        pool,
        task_id,
        "call_agent",
        Some("planner"),
        Some("need spec for task"),
        Some(&result.status.state),
    )
    .await?;

    if result.status.state == "completed" {
        // Parse spec from result
        let spec_text = a2a::extract_result_text(&result)
            .unwrap_or_else(|| "{}".to_string());

        let spec_value: Value = serde_json::from_str(&spec_text).unwrap_or_else(|_| {
            serde_json::json!({
                "title": task.input,
                "description": spec_text,
                "acceptance_criteria": [],
                "test_plan": {"unit_tests": [], "commands": []},
                "files_likely_affected": [],
                "domains": []
            })
        });

        let title = spec_value["title"]
            .as_str()
            .unwrap_or(&task.input)
            .to_string();
        let description = spec_value["description"]
            .as_str()
            .unwrap_or("")
            .to_string();
        let acceptance_criteria: Vec<String> = spec_value["acceptance_criteria"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();
        let test_plan = spec_value
            .get("test_plan")
            .cloned()
            .unwrap_or(serde_json::json!({}));
        let files_affected: Vec<String> = spec_value["files_likely_affected"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();
        let domains: Vec<String> = spec_value["domains"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();

        db::create_task_spec(
            pool,
            task_id,
            &title,
            &description,
            &acceptance_criteria,
            &test_plan,
            Some(&files_affected),
            Some(&domains),
        )
        .await?;

        event_bus
            .emit_step_finished(task_id, "spec", "completed", duration_ms)
            .await?;

        Ok(Phase::AwaitSpecApproval)
    } else if result.status.state == "failed" {
        event_bus
            .emit_step_finished(task_id, "spec", "failed", duration_ms)
            .await?;
        Err(anyhow!("planner agent failed"))
    } else {
        event_bus
            .emit_step_finished(task_id, "spec", &result.status.state, duration_ms)
            .await?;
        Err(anyhow!(
            "unexpected planner state: {}",
            result.status.state
        ))
    }
}

/// Call the bootstrapper agent to prepare the workspace.
async fn run_bootstrap_phase(
    pool: &PgPool,
    config: &Config,
    a2a_client: &A2AClient,
    event_bus: &EventBus,
    metrics: &Metrics,
    task_id: &str,
) -> Result<Phase> {
    let step_start = Instant::now();
    event_bus
        .emit_step_started(task_id, "bootstrap", "bootstrapper")
        .await?;

    let task = db::get_task(pool, task_id)
        .await?
        .ok_or_else(|| anyhow!("task not found"))?;

    let spec = db::get_task_spec(pool, task_id)
        .await?
        .ok_or_else(|| anyhow!("no spec found for task"))?;

    let bootstrapper = find_agent(pool, "bootstrapper").await?;

    let message = format!(
        "Prepare a workspace for this task.\n\n\
         Task: {}\n\
         Spec title: {}\n\
         Spec description: {}\n\
         Domains: {:?}\n\
         Files likely affected: {:?}\n\n\
         Set up the development environment with all necessary tools, \
         dependencies, and configurations.",
        task.input,
        spec.title,
        spec.description,
        spec.domains,
        spec.files_affected,
    );

    let metadata = serde_json::json!({
        "task_id": task_id,
        "project_id": task.project_id,
    });

    let result = call_agent_with_retry(
        a2a_client,
        &bootstrapper.url,
        &bootstrapper.jwt_token,
        task_id,
        &message,
        Some(metadata),
        config.retry_max,
        config.retry_delay_s,
        metrics,
        &bootstrapper.name,
    )
    .await?;

    let duration_ms = step_start.elapsed().as_millis() as u64;

    db::create_supervisor_decision(
        pool,
        task_id,
        "call_agent",
        Some("bootstrapper"),
        Some("preparing workspace"),
        Some(&result.status.state),
    )
    .await?;

    if result.status.state == "completed" {
        // Extract workspace_id from result
        let result_text = a2a::extract_result_text(&result).unwrap_or_default();
        let result_value: Value =
            serde_json::from_str(&result_text).unwrap_or(serde_json::json!({}));
        let fallback_ws_id = format!("ws-{}", task_id);
        let workspace_id = result_value["workspace_id"]
            .as_str()
            .unwrap_or(&fallback_ws_id);
        db::update_task_workspace(pool, task_id, workspace_id).await?;

        event_bus
            .emit_step_finished(task_id, "bootstrap", "completed", duration_ms)
            .await?;

        Ok(Phase::Work)
    } else {
        event_bus
            .emit_step_finished(task_id, "bootstrap", "failed", duration_ms)
            .await?;
        Err(anyhow!("bootstrapper agent failed"))
    }
}

/// Call the worker agent to implement the task.
async fn run_work_phase(
    pool: &PgPool,
    config: &Config,
    a2a_client: &A2AClient,
    event_bus: &EventBus,
    metrics: &Metrics,
    task_id: &str,
) -> Result<Phase> {
    let step_start = Instant::now();
    event_bus
        .emit_step_started(task_id, "work", "worker")
        .await?;

    let task = db::get_task(pool, task_id)
        .await?
        .ok_or_else(|| anyhow!("task not found"))?;

    let spec = db::get_task_spec(pool, task_id)
        .await?
        .ok_or_else(|| anyhow!("no spec found for task"))?;

    let worker = find_agent(pool, "worker").await?;

    // Build message with spec and any previous review feedback
    let mut message = format!(
        "Implement the following specification.\n\n\
         ## Spec\n\
         Title: {}\n\
         Description: {}\n\n\
         ## Acceptance Criteria\n{}\n\n\
         ## Test Plan\n{}\n",
        spec.title,
        spec.description,
        spec.acceptance_criteria
            .iter()
            .map(|c| format!("- {}", c))
            .collect::<Vec<_>>()
            .join("\n"),
        serde_json::to_string_pretty(&spec.test_plan).unwrap_or_default(),
    );

    // Append review feedback if this is a re-work iteration
    if let Some(review) = db::get_latest_review(pool, task_id).await? {
        if review.verdict == "FAIL" {
            message.push_str(&format!(
                "\n\n## Previous Review Feedback\n\
                 Verdict: FAIL\n\
                 Summary: {}\n\
                 Findings: {}\n\n\
                 Please address the above findings.",
                review.summary.unwrap_or_default(),
                serde_json::to_string_pretty(&review.findings).unwrap_or_default(),
            ));
        }
    }

    let metadata = serde_json::json!({
        "task_id": task_id,
        "workspace_id": task.workspace_id,
        "iteration": task.iteration,
    });

    let result = call_agent_with_retry(
        a2a_client,
        &worker.url,
        &worker.jwt_token,
        task_id,
        &message,
        Some(metadata),
        config.retry_max,
        config.retry_delay_s,
        metrics,
        &worker.name,
    )
    .await?;

    let duration_ms = step_start.elapsed().as_millis() as u64;

    db::create_supervisor_decision(
        pool,
        task_id,
        "call_agent",
        Some("worker"),
        Some("implementing task"),
        Some(&result.status.state),
    )
    .await?;

    if result.status.state == "completed" {
        // Store artifacts
        let result_text = a2a::extract_result_text(&result).unwrap_or_default();
        let result_value: Value =
            serde_json::from_str(&result_text).unwrap_or(serde_json::json!({}));

        let files_changed: Vec<String> = result_value["files_changed"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();

        db::create_task_artifact(
            pool,
            task_id,
            task.iteration,
            "code_diff",
            task.workspace_id.as_deref(),
            Some(&files_changed),
            Some(&result_text),
        )
        .await?;

        event_bus
            .emit_step_finished(task_id, "work", "completed", duration_ms)
            .await?;

        Ok(Phase::Review)
    } else if result.status.state == "input-required" || result.status.state == "input_required" {
        // Worker needs input -- for PoC, escalate to user
        event_bus
            .emit_step_finished(task_id, "work", "input_required", duration_ms)
            .await?;

        let question = a2a::extract_result_text(&result)
            .unwrap_or_else(|| "Worker needs additional input".to_string());
        db::update_task_status(pool, task_id, "awaiting_input", None).await?;
        event_bus
            .emit_awaiting_input(task_id, "worker_question", None, Some(question), None)
            .await?;

        // Poll for user input
        loop {
            tokio::time::sleep(Duration::from_secs(2)).await;
            let current_task = db::get_task(pool, task_id)
                .await?
                .ok_or_else(|| anyhow!("task not found"))?;
            if current_task.status == "cancelled" {
                return Ok(Phase::Completed);
            }
            if current_task.status == "running" {
                // User provided input, retry work phase
                return Ok(Phase::Work);
            }
        }
    } else {
        event_bus
            .emit_step_finished(task_id, "work", "failed", duration_ms)
            .await?;
        Err(anyhow!("worker agent failed: {}", result.status.state))
    }
}

/// Call the reviewer agent to check the result.
async fn run_review_phase(
    pool: &PgPool,
    config: &Config,
    a2a_client: &A2AClient,
    event_bus: &EventBus,
    metrics: &Metrics,
    task_id: &str,
    iteration: i32,
) -> Result<Phase> {
    let step_start = Instant::now();
    event_bus
        .emit_step_started(task_id, "review", "reviewer")
        .await?;

    let task = db::get_task(pool, task_id)
        .await?
        .ok_or_else(|| anyhow!("task not found"))?;

    let spec = db::get_task_spec(pool, task_id)
        .await?
        .ok_or_else(|| anyhow!("no spec found for task"))?;

    let artifacts = db::get_task_artifacts(pool, task_id).await?;
    let reviewer = find_agent(pool, "reviewer").await?;

    let artifacts_summary: Vec<Value> = artifacts
        .iter()
        .map(|a| {
            serde_json::json!({
                "type": a.artifact_type,
                "path": a.path,
                "files_changed": a.files_changed,
                "content": a.content,
            })
        })
        .collect();

    let message = format!(
        "Review the implementation against the specification.\n\n\
         ## Spec\n\
         Title: {}\n\
         Description: {}\n\n\
         ## Acceptance Criteria\n{}\n\n\
         ## Test Plan\n{}\n\n\
         ## Artifacts\n{}\n\n\
         Respond with a JSON object containing:\n\
         - verdict: \"PASS\" or \"FAIL\"\n\
         - findings: array of {{severity, file, line, message, evidence}}\n\
         - summary: string",
        spec.title,
        spec.description,
        spec.acceptance_criteria
            .iter()
            .map(|c| format!("- {}", c))
            .collect::<Vec<_>>()
            .join("\n"),
        serde_json::to_string_pretty(&spec.test_plan).unwrap_or_default(),
        serde_json::to_string_pretty(&artifacts_summary).unwrap_or_default(),
    );

    let metadata = serde_json::json!({
        "task_id": task_id,
        "workspace_id": task.workspace_id,
        "iteration": iteration,
    });

    let result = call_agent_with_retry(
        a2a_client,
        &reviewer.url,
        &reviewer.jwt_token,
        task_id,
        &message,
        Some(metadata),
        config.retry_max,
        config.retry_delay_s,
        metrics,
        &reviewer.name,
    )
    .await?;

    let duration_ms = step_start.elapsed().as_millis() as u64;

    db::create_supervisor_decision(
        pool,
        task_id,
        "call_agent",
        Some("reviewer"),
        Some("reviewing implementation"),
        Some(&result.status.state),
    )
    .await?;

    if result.status.state == "completed" {
        let result_text = a2a::extract_result_text(&result).unwrap_or_default();
        let review_value: Value =
            serde_json::from_str(&result_text).unwrap_or(serde_json::json!({"verdict": "FAIL", "summary": result_text}));

        let verdict = review_value["verdict"]
            .as_str()
            .unwrap_or("FAIL")
            .to_string();
        let findings = review_value.get("findings").cloned();
        let summary = review_value["summary"].as_str().map(|s| s.to_string());

        db::create_task_review(
            pool,
            task_id,
            iteration,
            &verdict,
            findings.as_ref(),
            summary.as_deref(),
        )
        .await?;

        if verdict == "PASS" {
            event_bus
                .emit_step_finished(task_id, "review", "completed", duration_ms)
                .await?;
            Ok(Phase::Completed)
        } else {
            warn!(task_id = %task_id, iteration = iteration, "review FAIL, sending back to worker");
            event_bus
                .emit_step_finished(task_id, "review", "rejected", duration_ms)
                .await?;
            Ok(Phase::Work)
        }
    } else {
        event_bus
            .emit_step_finished(task_id, "review", "failed", duration_ms)
            .await?;
        Err(anyhow!("reviewer agent failed"))
    }
}

/// Find a registered healthy agent by name.
async fn find_agent(pool: &PgPool, name: &str) -> Result<db::Agent> {
    db::get_agent_by_name(pool, name)
        .await?
        .ok_or_else(|| anyhow!("agent '{}' not found or unhealthy", name))
}

/// Call an agent with retries.
async fn call_agent_with_retry(
    a2a_client: &A2AClient,
    agent_url: &str,
    jwt_token: &str,
    task_id: &str,
    message: &str,
    metadata: Option<Value>,
    max_retries: i32,
    retry_delay_s: u64,
    metrics: &Metrics,
    agent_name: &str,
) -> Result<a2a::A2ATaskResult> {
    let mut last_error = None;
    let mut delay = Duration::from_secs(retry_delay_s);

    for attempt in 0..=max_retries {
        if attempt > 0 {
            warn!(
                task_id = %task_id,
                agent = %agent_name,
                attempt = attempt,
                "retrying A2A call after {:?}",
                delay
            );
            tokio::time::sleep(delay).await;
            delay *= 2; // Exponential backoff
        }

        metrics
            .agent_calls
            .with_label_values(&[agent_name, "tasks/send"])
            .inc();

        let timer = metrics
            .agent_call_duration
            .with_label_values(&[agent_name])
            .start_timer();

        match a2a_client
            .send_task(agent_url, jwt_token, task_id, message, metadata.clone())
            .await
        {
            Ok(result) => {
                timer.observe_duration();
                return Ok(result);
            }
            Err(e) => {
                timer.observe_duration();
                metrics
                    .agent_call_errors
                    .with_label_values(&[agent_name, "request_error"])
                    .inc();
                warn!(
                    task_id = %task_id,
                    agent = %agent_name,
                    attempt = attempt,
                    error = %e,
                    "A2A call failed"
                );
                last_error = Some(e);
            }
        }
    }

    Err(last_error.unwrap_or_else(|| anyhow!("all retries exhausted")))
}
