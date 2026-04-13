//! LLM-driven supervisor agent.
//!
//! The supervisor is a tool-calling LLM agent that drives task lifecycle
//! by reasoning about what to do next and executing platform-internal tools.
//! This replaces the hardcoded phase state machine with genuine LLM decisions.

use anyhow::{anyhow, Result};
use regex::Regex;
use serde_json::{json, Value};
use sqlx::PgPool;
use std::time::{Duration, Instant};
use tracing::{error, info, warn};

use crate::a2a::A2AClient;
use crate::agui::EventBus;
use crate::config::Config;
use crate::db;
use crate::metrics::Metrics;
use crate::supervisor_tools::{self, ToolExecutor};

/// System prompt for the supervisor agent.
const SYSTEM_PROMPT: &str = include_str!("../../prompts/supervisor/v1/system.md");

/// Maximum turns (LLM calls) before forcefully terminating the loop.
const MAX_TURNS: usize = 30;

/// Run the supervisor for a task. Spawned as a tokio task from the API handler.
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
            info!(task_id = %task_id, "supervisor completed");
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

    event_bus.remove_channel(&task_id).await;
}

/// The core agentic loop: call LLM with tools, execute tool calls, repeat.
async fn supervisor_loop(
    pool: &PgPool,
    config: &Config,
    a2a_client: &A2AClient,
    event_bus: &EventBus,
    metrics: &Metrics,
    task_id: &str,
) -> Result<()> {
    // Transition to running
    db::update_task_status(pool, task_id, "running", None).await?;
    event_bus.emit_run_started(task_id).await?;

    let task_start = Instant::now();
    let max_duration = Duration::from_secs(config.max_task_duration_s as u64);

    // Load the task to build the initial user message
    let task = db::get_task(pool, task_id)
        .await?
        .ok_or_else(|| anyhow!("task not found"))?;

    // Build the tool executor (has mutable state: `finished` flag)
    let mut executor = ToolExecutor::new(
        pool.clone(),
        config.clone(),
        a2a_client.clone(),
        event_bus.clone(),
        metrics.clone(),
        task_id.to_string(),
    );

    // Conversation history for the LLM
    let mut messages: Vec<Value> = vec![
        json!({"role": "system", "content": SYSTEM_PROMPT}),
        json!({"role": "user", "content": format!(
            "New task assigned.\n\nTask ID: {}\nTask input: {}\nProject: {}\n\n\
             Start by reading the task state, then proceed with the standard flow.",
            task_id,
            task.input,
            task.project_id.as_deref().unwrap_or("none"),
        )}),
    ];

    let tools = supervisor_tools::tool_definitions();
    let http_client = reqwest::Client::builder()
        .timeout(Duration::from_secs(config.phase_timeout_s as u64))
        .build()?;

    let think_re = Regex::new(r"(?s)<think>.*?</think>").unwrap();

    for turn in 0..MAX_TURNS {
        // Check timeout
        if task_start.elapsed() > max_duration {
            db::update_task_status(pool, task_id, "failed", Some("task duration exceeded")).await?;
            event_bus
                .emit_run_error(task_id, "task duration exceeded", None)
                .await?;
            metrics
                .tasks_failed
                .with_label_values(&["timeout"])
                .inc();
            return Ok(());
        }

        // Check cancellation
        let current = db::get_task(pool, task_id)
            .await?
            .ok_or_else(|| anyhow!("task not found"))?;
        if current.status == "cancelled" {
            info!(task_id = %task_id, "task cancelled during supervision");
            return Ok(());
        }

        // Call the LLM
        info!(task_id = %task_id, turn, "supervisor LLM call");

        let llm_request = json!({
            "model": &config.supervisor_model,
            "messages": messages,
            "tools": tools,
            "temperature": 0.1,
            "max_tokens": 4096,
        });

        let llm_response = http_client
            .post(format!("{}/v1/chat/completions", config.llm_router_url))
            .header("Content-Type", "application/json")
            .header("Authorization", format!("Bearer {}", config.jwt_secret))
            .header("X-Agent-ID", "supervisor")
            .header("X-Task-ID", task_id)
            .json(&llm_request)
            .send()
            .await
            .map_err(|e| anyhow!("LLM request failed: {}", e))?;

        if !llm_response.status().is_success() {
            let status = llm_response.status();
            let body = llm_response.text().await.unwrap_or_default();
            return Err(anyhow!("LLM returned {}: {}", status, &body[..body.len().min(500)]));
        }

        let response_body: Value = llm_response
            .json()
            .await
            .map_err(|e| anyhow!("failed to parse LLM response: {}", e))?;

        let choice = response_body["choices"]
            .as_array()
            .and_then(|c| c.first())
            .ok_or_else(|| anyhow!("no choices in LLM response"))?;

        let assistant_msg = &choice["message"];
        let finish_reason = choice["finish_reason"].as_str().unwrap_or("");

        // Add assistant message to conversation
        messages.push(assistant_msg.clone());

        // Check for tool calls
        let tool_calls = assistant_msg["tool_calls"].as_array();

        if let Some(calls) = tool_calls {
            if calls.is_empty() {
                // No tool calls — LLM is done talking
                break;
            }

            for call in calls {
                let call_id = call["id"].as_str().unwrap_or("");
                let func_name = call["function"]["name"].as_str().unwrap_or("");
                let func_args_str = call["function"]["arguments"].as_str().unwrap_or("{}");

                let func_args: Value =
                    serde_json::from_str(func_args_str).unwrap_or(json!({}));

                info!(
                    task_id = %task_id,
                    tool = func_name,
                    turn,
                    "executing supervisor tool"
                );

                let tool_result = executor.execute(func_name, &func_args).await;

                // Strip think tags from tool results (shouldn't happen but just in case)
                let clean_result = think_re.replace_all(&tool_result, "").to_string();

                // Append tool result to conversation
                messages.push(json!({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": clean_result,
                }));

                // If executor flagged as finished (complete_task or fail_task called)
                if executor.finished {
                    info!(task_id = %task_id, turn, tool = func_name, "supervisor finished via tool");
                    return Ok(());
                }
            }

            metrics
                .supervisor_iterations
                .with_label_values(&["tool_call"])
                .inc();
        } else {
            // No tool_calls field — LLM gave a text-only response
            if let Some(content) = assistant_msg["content"].as_str() {
                let clean = think_re.replace_all(content, "").trim().to_string();
                if !clean.is_empty() {
                    info!(task_id = %task_id, turn, "supervisor text: {}", &clean[..clean.len().min(200)]);
                }
            }

            // If finish_reason is "stop" and no tools, the LLM decided to stop
            if finish_reason == "stop" {
                warn!(task_id = %task_id, turn, "supervisor stopped without calling complete_task or fail_task");
                // Force completion check: if there's a PASS review, complete; otherwise fail
                if let Some(review) = db::get_latest_review(pool, task_id).await? {
                    if review.verdict == "PASS" {
                        executor.execute("complete_task", &json!({})).await;
                        return Ok(());
                    }
                }
                db::update_task_status(
                    pool,
                    task_id,
                    "failed",
                    Some("supervisor ended without resolution"),
                )
                .await?;
                event_bus
                    .emit_run_error(task_id, "supervisor ended without resolution", None)
                    .await?;
                return Ok(());
            }
        }
    }

    // Max turns exceeded
    warn!(task_id = %task_id, "supervisor hit max turns ({})", MAX_TURNS);
    db::update_task_status(pool, task_id, "failed", Some("supervisor max turns exceeded")).await?;
    event_bus
        .emit_run_error(task_id, "supervisor max turns exceeded", None)
        .await?;
    metrics
        .tasks_failed
        .with_label_values(&["max_turns"])
        .inc();
    Ok(())
}
