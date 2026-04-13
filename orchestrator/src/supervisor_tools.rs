//! Supervisor tool definitions and execution.
//!
//! The supervisor is an LLM-driven agent with tools for platform-internal
//! operations: reading task state, dispatching agents, controlling lifecycle.

use anyhow::{anyhow, Result};
use serde_json::{json, Value};
use sqlx::PgPool;
use std::time::Duration;
use tracing::{info, warn};

use crate::a2a::{self, A2AClient};
use crate::agui::EventBus;
use crate::config::Config;
use crate::db;
use crate::metrics::Metrics;

// ─── Tool schemas (OpenAI function-calling format) ──────────────────────────

pub fn tool_definitions() -> Vec<Value> {
    vec![
        json!({
            "type": "function",
            "function": {
                "name": "get_task",
                "description": "Read current task state: input, status, workspace_id, iteration, error.",
                "parameters": { "type": "object", "properties": {}, "required": [] }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "get_spec",
                "description": "Read the current spec for this task. Returns null if no spec exists.",
                "parameters": { "type": "object", "properties": {}, "required": [] }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "get_budget",
                "description": "Read remaining budget: tokens, calls, iterations left, time left.",
                "parameters": { "type": "object", "properties": {}, "required": [] }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "get_artifacts",
                "description": "Read the latest code artifacts produced by the worker.",
                "parameters": { "type": "object", "properties": {}, "required": [] }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "get_latest_review",
                "description": "Read the latest reviewer verdict, findings, and summary. Returns null if no review yet.",
                "parameters": { "type": "object", "properties": {}, "required": [] }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "dispatch_agent",
                "description": "Call an agent via A2A and wait for its response. Returns the agent's JSON result or an error message.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_name": {
                            "type": "string",
                            "description": "Agent to call: 'planner', 'bootstrapper', 'worker', 'reviewer'",
                            "enum": ["planner", "bootstrapper", "worker", "reviewer"]
                        },
                        "message": {
                            "type": "string",
                            "description": "Full message/prompt to send to the agent. Include all context it needs."
                        }
                    },
                    "required": ["agent_name", "message"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "save_spec",
                "description": "Store a spec returned by the planner. Pass the complete spec JSON from the planner's response.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "spec_json": {
                            "type": "string",
                            "description": "Complete spec JSON string as returned by the planner"
                        }
                    },
                    "required": ["spec_json"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "request_user_approval",
                "description": "Show the spec to the user and wait for approval. Blocks until the user approves or rejects. Returns 'approved' or 'rejected'.",
                "parameters": { "type": "object", "properties": {}, "required": [] }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "store_artifacts",
                "description": "Store worker artifacts (code changes, test results) in the database.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "result_json": {
                            "type": "string",
                            "description": "Complete worker result JSON string"
                        }
                    },
                    "required": ["result_json"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "store_review",
                "description": "Store a reviewer's verdict and findings.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "review_json": {
                            "type": "string",
                            "description": "Complete review JSON string with verdict, findings, summary"
                        }
                    },
                    "required": ["review_json"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "emit_event",
                "description": "Emit an AG-UI event to the WebUI for real-time updates.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "event_type": {
                            "type": "string",
                            "description": "Event type: 'step_started', 'step_finished', 'text_message'",
                            "enum": ["step_started", "step_finished", "text_message"]
                        },
                        "step": {
                            "type": "string",
                            "description": "Step name: 'spec', 'bootstrap', 'work', 'review'"
                        },
                        "status": {
                            "type": "string",
                            "description": "Step status (for step_finished): 'completed', 'failed', 'rejected'"
                        },
                        "message": {
                            "type": "string",
                            "description": "Message content (for text_message events)"
                        }
                    },
                    "required": ["event_type"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "complete_task",
                "description": "Mark the task as successfully completed. Call this when the reviewer gives PASS.",
                "parameters": { "type": "object", "properties": {}, "required": [] }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "fail_task",
                "description": "Mark the task as failed with a reason.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Why the task failed"
                        }
                    },
                    "required": ["reason"]
                }
            }
        }),
    ]
}

// ─── Tool executor ──────────────────────────────────────────────────────────

/// Executes supervisor tools against the platform's internal state.
pub struct ToolExecutor {
    pool: PgPool,
    config: Config,
    a2a_client: A2AClient,
    event_bus: EventBus,
    metrics: Metrics,
    task_id: String,
    /// Set to true when complete_task or fail_task is called
    pub finished: bool,
}

impl ToolExecutor {
    pub fn new(
        pool: PgPool,
        config: Config,
        a2a_client: A2AClient,
        event_bus: EventBus,
        metrics: Metrics,
        task_id: String,
    ) -> Self {
        Self {
            pool,
            config,
            a2a_client,
            event_bus,
            metrics,
            task_id,
            finished: false,
        }
    }

    /// Execute a tool call and return the result as a string.
    pub async fn execute(&mut self, name: &str, args: &Value) -> String {
        let result = match name {
            "get_task" => self.tool_get_task().await,
            "get_spec" => self.tool_get_spec().await,
            "get_budget" => self.tool_get_budget().await,
            "get_artifacts" => self.tool_get_artifacts().await,
            "get_latest_review" => self.tool_get_latest_review().await,
            "dispatch_agent" => self.tool_dispatch_agent(args).await,
            "save_spec" => self.tool_save_spec(args).await,
            "request_user_approval" => self.tool_request_user_approval().await,
            "store_artifacts" => self.tool_store_artifacts(args).await,
            "store_review" => self.tool_store_review(args).await,
            "emit_event" => self.tool_emit_event(args).await,
            "complete_task" => self.tool_complete_task().await,
            "fail_task" => self.tool_fail_task(args).await,
            _ => Err(anyhow!("unknown tool: {}", name)),
        };

        match result {
            Ok(s) => s,
            Err(e) => format!("ERROR: {}", e),
        }
    }

    // ── Individual tool implementations ─────────────────────────────────

    async fn tool_get_task(&self) -> Result<String> {
        let task = db::get_task(&self.pool, &self.task_id)
            .await?
            .ok_or_else(|| anyhow!("task not found"))?;
        Ok(serde_json::to_string(&json!({
            "id": task.id,
            "input": task.input,
            "status": task.status,
            "project_id": task.project_id,
            "workspace_id": task.workspace_id,
            "iteration": task.iteration,
            "error": task.error,
        }))?)
    }

    async fn tool_get_spec(&self) -> Result<String> {
        match db::get_task_spec(&self.pool, &self.task_id).await? {
            Some(spec) => Ok(serde_json::to_string(&json!({
                "title": spec.title,
                "description": spec.description,
                "acceptance_criteria": spec.acceptance_criteria,
                "test_plan": spec.test_plan,
                "files_affected": spec.files_affected,
                "domains": spec.domains,
                "status": spec.status,
            }))?),
            None => Ok("null".to_string()),
        }
    }

    async fn tool_get_budget(&self) -> Result<String> {
        match db::get_task_budget(&self.pool, &self.task_id).await? {
            Some(b) => Ok(serde_json::to_string(&json!({
                "max_tokens": b.max_tokens,
                "spent_tokens": b.spent_tokens,
                "remaining_tokens": b.max_tokens - b.spent_tokens,
                "max_iterations": b.max_iterations,
                "max_calls": b.max_calls,
                "spent_calls": b.spent_calls,
            }))?),
            None => Ok(json!({"max_tokens": 500000, "spent_tokens": 0, "remaining_tokens": 500000}).to_string()),
        }
    }

    async fn tool_get_artifacts(&self) -> Result<String> {
        let artifacts = db::get_task_artifacts(&self.pool, &self.task_id).await?;
        let summary: Vec<Value> = artifacts
            .iter()
            .map(|a| json!({
                "type": a.artifact_type,
                "path": a.path,
                "files_changed": a.files_changed,
                "iteration": a.iteration,
                "content": a.content.as_deref().map(|c| if c.len() > 2000 { &c[..2000] } else { c }),
            }))
            .collect();
        Ok(serde_json::to_string(&summary)?)
    }

    async fn tool_get_latest_review(&self) -> Result<String> {
        match db::get_latest_review(&self.pool, &self.task_id).await? {
            Some(r) => Ok(serde_json::to_string(&json!({
                "verdict": r.verdict,
                "findings": r.findings,
                "summary": r.summary,
                "iteration": r.iteration,
            }))?),
            None => Ok("null".to_string()),
        }
    }

    async fn tool_dispatch_agent(&self, args: &Value) -> Result<String> {
        let agent_name = args["agent_name"]
            .as_str()
            .ok_or_else(|| anyhow!("missing agent_name"))?;
        let message = args["message"]
            .as_str()
            .ok_or_else(|| anyhow!("missing message"))?;

        info!(task_id = %self.task_id, agent = agent_name, "dispatching agent");

        // Look up the agent
        let agent = db::get_agent_by_name(&self.pool, agent_name)
            .await?
            .ok_or_else(|| anyhow!("agent '{}' not found or unhealthy", agent_name))?;

        // Record decision
        db::create_supervisor_decision(
            &self.pool,
            &self.task_id,
            "dispatch_agent",
            Some(agent_name),
            Some(&message[..message.len().min(200)]),
            None,
        )
        .await?;

        // Emit step_started event
        self.event_bus
            .emit_step_started(&self.task_id, agent_name, agent_name)
            .await?;

        // Call agent with retry
        self.metrics
            .agent_calls
            .with_label_values(&[agent_name, "tasks/send"])
            .inc();

        let timer = self
            .metrics
            .agent_call_duration
            .with_label_values(&[agent_name])
            .start_timer();

        let metadata = json!({
            "task_id": self.task_id,
        });

        let mut last_error = None;
        let mut delay = Duration::from_secs(self.config.retry_delay_s);

        for attempt in 0..=self.config.retry_max {
            if attempt > 0 {
                warn!(task_id = %self.task_id, agent = agent_name, attempt, "retrying dispatch");
                tokio::time::sleep(delay).await;
                delay *= 2;
            }

            match self
                .a2a_client
                .send_task(
                    &agent.url,
                    &agent.jwt_token,
                    &self.task_id,
                    message,
                    Some(metadata.clone()),
                )
                .await
            {
                Ok(result) => {
                    timer.observe_duration();

                    let state = &result.status.state;
                    let result_text =
                        a2a::extract_result_text(&result).unwrap_or_else(|| "{}".to_string());

                    // Update decision with result
                    db::create_supervisor_decision(
                        &self.pool,
                        &self.task_id,
                        "agent_result",
                        Some(agent_name),
                        Some(&format!("state={}", state)),
                        Some(state),
                    )
                    .await?;

                    // Emit step_finished
                    self.event_bus
                        .emit_step_finished(&self.task_id, agent_name, state, 0)
                        .await?;

                    return Ok(json!({
                        "state": state,
                        "result": result_text,
                    })
                    .to_string());
                }
                Err(e) => {
                    self.metrics
                        .agent_call_errors
                        .with_label_values(&[agent_name, "request_error"])
                        .inc();
                    last_error = Some(e);
                }
            }
        }

        timer.observe_duration();
        let err = last_error.unwrap_or_else(|| anyhow!("all retries exhausted"));
        Ok(json!({
            "state": "failed",
            "error": err.to_string(),
        })
        .to_string())
    }

    async fn tool_save_spec(&self, args: &Value) -> Result<String> {
        let spec_str = args["spec_json"]
            .as_str()
            .ok_or_else(|| anyhow!("missing spec_json"))?;
        let spec: Value = serde_json::from_str(spec_str)
            .unwrap_or_else(|_| json!({"title": "", "description": spec_str}));

        let title = spec["title"].as_str().unwrap_or("").to_string();
        let description = spec["description"].as_str().unwrap_or("").to_string();
        let acceptance_criteria: Vec<String> = spec["acceptance_criteria"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();
        let test_plan = spec.get("test_plan").cloned().unwrap_or(json!({}));
        let files_affected: Vec<String> = spec["files_likely_affected"]
            .as_array()
            .or_else(|| spec["files_affected"].as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();
        let domains: Vec<String> = spec["domains"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();

        db::create_task_spec(
            &self.pool,
            &self.task_id,
            &title,
            &description,
            &acceptance_criteria,
            &test_plan,
            Some(&files_affected),
            Some(&domains),
        )
        .await?;

        Ok("OK: spec saved".to_string())
    }

    async fn tool_request_user_approval(&self) -> Result<String> {
        let spec = db::get_task_spec(&self.pool, &self.task_id).await?;
        let spec_value = spec.map(|s| {
            json!({
                "title": s.title,
                "description": s.description,
                "acceptance_criteria": s.acceptance_criteria,
                "test_plan": s.test_plan,
            })
        });

        db::update_task_status(&self.pool, &self.task_id, "awaiting_input", None).await?;
        self.event_bus
            .emit_awaiting_input(
                &self.task_id,
                "spec_approval",
                spec_value,
                None,
                Some(vec!["approve".to_string(), "reject".to_string()]),
            )
            .await?;

        // Poll for user response
        let timeout = Duration::from_secs(self.config.phase_timeout_s as u64);
        let poll_start = std::time::Instant::now();

        loop {
            tokio::time::sleep(Duration::from_secs(2)).await;

            // Check cancellation
            let task = db::get_task(&self.pool, &self.task_id)
                .await?
                .ok_or_else(|| anyhow!("task not found"))?;
            if task.status == "cancelled" {
                return Ok("cancelled".to_string());
            }

            // Check spec status
            if let Some(spec) = db::get_task_spec(&self.pool, &self.task_id).await? {
                if spec.status == "approved" {
                    db::update_task_status(&self.pool, &self.task_id, "running", None).await?;
                    return Ok("approved".to_string());
                } else if spec.status == "rejected" {
                    return Ok("rejected".to_string());
                }
            }

            if poll_start.elapsed() > timeout {
                return Ok("timeout".to_string());
            }
        }
    }

    async fn tool_store_artifacts(&self, args: &Value) -> Result<String> {
        let result_str = args["result_json"]
            .as_str()
            .ok_or_else(|| anyhow!("missing result_json"))?;
        let result: Value = serde_json::from_str(result_str).unwrap_or(json!({}));

        let task = db::get_task(&self.pool, &self.task_id)
            .await?
            .ok_or_else(|| anyhow!("task not found"))?;

        let files_changed: Vec<String> = if let Some(arr) = result["files_changed"].as_array() {
            arr.iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect()
        } else if let Some(arts) = result["artifacts"].as_array() {
            arts.iter()
                .filter_map(|a| a["path"].as_str().map(|s| s.to_string()))
                .collect()
        } else {
            Vec::new()
        };

        db::create_task_artifact(
            &self.pool,
            &self.task_id,
            task.iteration,
            "code_diff",
            task.workspace_id.as_deref(),
            Some(&files_changed),
            Some(result_str),
        )
        .await?;

        Ok(format!("OK: stored artifacts ({} files)", files_changed.len()))
    }

    async fn tool_store_review(&self, args: &Value) -> Result<String> {
        let review_str = args["review_json"]
            .as_str()
            .ok_or_else(|| anyhow!("missing review_json"))?;
        let review: Value = serde_json::from_str(review_str).unwrap_or(json!({}));

        let task = db::get_task(&self.pool, &self.task_id)
            .await?
            .ok_or_else(|| anyhow!("task not found"))?;

        let verdict = review["verdict"].as_str().unwrap_or("FAIL").to_string();
        let findings = review.get("findings").cloned();
        let summary = review["summary"].as_str().map(|s| s.to_string());

        db::create_task_review(
            &self.pool,
            &self.task_id,
            task.iteration,
            &verdict,
            findings.as_ref(),
            summary.as_deref(),
        )
        .await?;

        Ok(format!("OK: stored review (verdict={})", verdict))
    }

    async fn tool_emit_event(&self, args: &Value) -> Result<String> {
        let event_type = args["event_type"].as_str().unwrap_or("");
        let step = args["step"].as_str().unwrap_or("");
        let status = args["status"].as_str().unwrap_or("completed");
        let message = args["message"].as_str().unwrap_or("");

        match event_type {
            "step_started" => {
                self.event_bus
                    .emit_step_started(&self.task_id, step, step)
                    .await?;
            }
            "step_finished" => {
                self.event_bus
                    .emit_step_finished(&self.task_id, step, status, 0)
                    .await?;
            }
            "text_message" => {
                // Use the text message event for progress updates
                let msg_id = format!("sup-{}", uuid::Uuid::new_v4());
                self.event_bus
                    .emit(
                        &self.task_id,
                        crate::agui::AgUiEvent::TextMessageContent {
                            message_id: msg_id,
                            delta: message.to_string(),
                        },
                    )
                    .await?;
            }
            _ => {
                return Ok(format!("unknown event_type: {}", event_type));
            }
        }
        Ok("OK".to_string())
    }

    async fn tool_complete_task(&mut self) -> Result<String> {
        db::update_task_status(&self.pool, &self.task_id, "completed", None).await?;

        let artifacts = db::get_task_artifacts(&self.pool, &self.task_id).await?;
        let artifact_values: Vec<Value> = artifacts
            .iter()
            .map(|a| json!({ "type": a.artifact_type, "path": a.path, "files_changed": a.files_changed }))
            .collect();

        self.event_bus
            .emit_run_finished(
                &self.task_id,
                "completed",
                if artifact_values.is_empty() {
                    None
                } else {
                    Some(artifact_values)
                },
            )
            .await?;

        self.metrics
            .tasks_completed
            .with_label_values(&["completed"])
            .inc();

        self.finished = true;
        Ok("OK: task completed".to_string())
    }

    async fn tool_fail_task(&mut self, args: &Value) -> Result<String> {
        let reason = args["reason"].as_str().unwrap_or("unknown error");

        db::update_task_status(&self.pool, &self.task_id, "failed", Some(reason)).await?;
        self.event_bus
            .emit_run_error(&self.task_id, reason, None)
            .await?;
        self.metrics
            .tasks_failed
            .with_label_values(&["supervisor_decision"])
            .inc();

        self.finished = true;
        Ok(format!("OK: task failed ({})", reason))
    }
}
