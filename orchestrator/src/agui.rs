use chrono::Utc;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sqlx::PgPool;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::{broadcast, RwLock};

/// AG-UI event types matching the spec.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum AgUiEvent {
    #[serde(rename = "RUN_STARTED")]
    RunStarted { task_id: String, timestamp: String },

    #[serde(rename = "STEP_STARTED")]
    StepStarted { step: String, agent_id: String },

    #[serde(rename = "STEP_FINISHED")]
    StepFinished {
        step: String,
        status: String,
        duration_ms: u64,
    },

    #[serde(rename = "RUN_AWAITING_INPUT")]
    RunAwaitingInput {
        input_type: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        spec: Option<Value>,
        #[serde(skip_serializing_if = "Option::is_none")]
        question: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        options: Option<Vec<String>>,
    },

    #[serde(rename = "TEXT_MESSAGE_START")]
    TextMessageStart { message_id: String, role: String },

    #[serde(rename = "TEXT_MESSAGE_CONTENT")]
    TextMessageContent { message_id: String, delta: String },

    #[serde(rename = "TEXT_MESSAGE_END")]
    TextMessageEnd { message_id: String },

    #[serde(rename = "STATE_SNAPSHOT")]
    StateSnapshot { data: Value },

    #[serde(rename = "STATE_DELTA")]
    StateDelta { path: String, value: Value },

    #[serde(rename = "RUN_FINISHED")]
    RunFinished {
        result: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        artifacts: Option<Vec<Value>>,
    },

    #[serde(rename = "RUN_ERROR")]
    RunError {
        error: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        context: Option<String>,
    },
}

/// An event with its sequence number, ready to be sent over SSE.
#[derive(Debug, Clone)]
pub struct SequencedEvent {
    pub task_id: String,
    pub seq: i32,
    pub event_type: String,
    pub event: AgUiEvent,
}

/// The event bus distributes AG-UI events to SSE subscribers and persists them in PostgreSQL.
#[derive(Clone)]
pub struct EventBus {
    pool: PgPool,
    /// Per-task broadcast channels. Each channel allows multiple SSE subscribers.
    channels: Arc<RwLock<HashMap<String, broadcast::Sender<SequencedEvent>>>>,
    /// Per-task sequence counter.
    sequences: Arc<RwLock<HashMap<String, i32>>>,
}

impl EventBus {
    pub fn new(pool: PgPool) -> Self {
        Self {
            pool,
            channels: Arc::new(RwLock::new(HashMap::new())),
            sequences: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    /// Subscribe to events for a specific task. Returns a broadcast receiver.
    pub async fn subscribe(&self, task_id: &str) -> broadcast::Receiver<SequencedEvent> {
        let mut channels = self.channels.write().await;
        let sender = channels
            .entry(task_id.to_string())
            .or_insert_with(|| {
                let (tx, _) = broadcast::channel(256);
                tx
            });
        sender.subscribe()
    }

    /// Emit an event for a task: persist to PostgreSQL and broadcast to subscribers.
    pub async fn emit(
        &self,
        task_id: &str,
        event: AgUiEvent,
    ) -> Result<i32, anyhow::Error> {
        // Get next sequence number
        let seq = {
            let mut sequences = self.sequences.write().await;
            let seq = sequences.entry(task_id.to_string()).or_insert(0);
            *seq += 1;
            *seq
        };

        let event_type = event_type_str(&event);
        let event_data = serde_json::to_value(&event)?;

        // Persist to PostgreSQL
        crate::db::create_task_event(&self.pool, task_id, seq, &event_type, &event_data).await?;

        let sequenced = SequencedEvent {
            task_id: task_id.to_string(),
            seq,
            event_type,
            event,
        };

        // Broadcast to subscribers (ignore errors if no subscribers)
        let channels = self.channels.read().await;
        if let Some(sender) = channels.get(task_id) {
            let _ = sender.send(sequenced);
        }

        Ok(seq)
    }

    /// Load the current sequence counter from the database (used on startup or reconnection).
    pub async fn load_sequence(&self, task_id: &str) -> Result<(), anyhow::Error> {
        let next_seq = crate::db::get_next_event_seq(&self.pool, task_id).await?;
        let mut sequences = self.sequences.write().await;
        sequences.insert(task_id.to_string(), next_seq - 1);
        Ok(())
    }

    /// Get historical events for replay (on SSE reconnection with Last-Event-ID).
    pub async fn replay_events(
        &self,
        task_id: &str,
        after_seq: i32,
    ) -> Result<Vec<SequencedEvent>, anyhow::Error> {
        let events = crate::db::get_task_events_after(&self.pool, task_id, after_seq).await?;
        let mut result = Vec::with_capacity(events.len());
        for ev in events {
            let agui_event: AgUiEvent = serde_json::from_value(ev.event_data)?;
            result.push(SequencedEvent {
                task_id: ev.task_id,
                seq: ev.seq,
                event_type: ev.event_type,
                event: agui_event,
            });
        }
        Ok(result)
    }

    /// Clean up channel for a completed/failed task.
    pub async fn remove_channel(&self, task_id: &str) {
        let mut channels = self.channels.write().await;
        channels.remove(task_id);
    }
}

/// Convenience helper: emit common events.
impl EventBus {
    pub async fn emit_run_started(&self, task_id: &str) -> Result<i32, anyhow::Error> {
        self.emit(
            task_id,
            AgUiEvent::RunStarted {
                task_id: task_id.to_string(),
                timestamp: Utc::now().to_rfc3339(),
            },
        )
        .await
    }

    pub async fn emit_step_started(
        &self,
        task_id: &str,
        step: &str,
        agent_id: &str,
    ) -> Result<i32, anyhow::Error> {
        self.emit(
            task_id,
            AgUiEvent::StepStarted {
                step: step.to_string(),
                agent_id: agent_id.to_string(),
            },
        )
        .await
    }

    pub async fn emit_step_finished(
        &self,
        task_id: &str,
        step: &str,
        status: &str,
        duration_ms: u64,
    ) -> Result<i32, anyhow::Error> {
        self.emit(
            task_id,
            AgUiEvent::StepFinished {
                step: step.to_string(),
                status: status.to_string(),
                duration_ms,
            },
        )
        .await
    }

    pub async fn emit_awaiting_input(
        &self,
        task_id: &str,
        input_type: &str,
        spec: Option<Value>,
        question: Option<String>,
        options: Option<Vec<String>>,
    ) -> Result<i32, anyhow::Error> {
        self.emit(
            task_id,
            AgUiEvent::RunAwaitingInput {
                input_type: input_type.to_string(),
                spec,
                question,
                options,
            },
        )
        .await
    }

    pub async fn emit_run_finished(
        &self,
        task_id: &str,
        result: &str,
        artifacts: Option<Vec<Value>>,
    ) -> Result<i32, anyhow::Error> {
        self.emit(
            task_id,
            AgUiEvent::RunFinished {
                result: result.to_string(),
                artifacts,
            },
        )
        .await
    }

    pub async fn emit_run_error(
        &self,
        task_id: &str,
        error: &str,
        context: Option<String>,
    ) -> Result<i32, anyhow::Error> {
        self.emit(
            task_id,
            AgUiEvent::RunError {
                error: error.to_string(),
                context,
            },
        )
        .await
    }
}

/// Extract the event type string for SSE event name.
fn event_type_str(event: &AgUiEvent) -> String {
    match event {
        AgUiEvent::RunStarted { .. } => "RUN_STARTED".to_string(),
        AgUiEvent::StepStarted { .. } => "STEP_STARTED".to_string(),
        AgUiEvent::StepFinished { .. } => "STEP_FINISHED".to_string(),
        AgUiEvent::RunAwaitingInput { .. } => "RUN_AWAITING_INPUT".to_string(),
        AgUiEvent::TextMessageStart { .. } => "TEXT_MESSAGE_START".to_string(),
        AgUiEvent::TextMessageContent { .. } => "TEXT_MESSAGE_CONTENT".to_string(),
        AgUiEvent::TextMessageEnd { .. } => "TEXT_MESSAGE_END".to_string(),
        AgUiEvent::StateSnapshot { .. } => "STATE_SNAPSHOT".to_string(),
        AgUiEvent::StateDelta { .. } => "STATE_DELTA".to_string(),
        AgUiEvent::RunFinished { .. } => "RUN_FINISHED".to_string(),
        AgUiEvent::RunError { .. } => "RUN_ERROR".to_string(),
    }
}
