use anyhow::{anyhow, Context, Result};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::time::Duration;
use uuid::Uuid;

/// A2A JSON-RPC 2.0 client for communicating with agents.
#[derive(Clone)]
pub struct A2AClient {
    http: Client,
}

// ─── JSON-RPC 2.0 types ─────────────────────────────────────────────────────

#[derive(Debug, Serialize)]
pub struct JsonRpcRequest {
    pub jsonrpc: String,
    pub method: String,
    pub params: TaskSendParams,
    pub id: String,
}

#[derive(Debug, Serialize)]
pub struct TaskSendParams {
    pub id: String,
    pub message: A2AMessage,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct A2AMessage {
    pub role: String,
    pub parts: Vec<A2APart>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum A2APart {
    #[serde(rename = "text")]
    Text { text: String },
}

#[derive(Debug, Deserialize)]
pub struct JsonRpcResponse {
    pub jsonrpc: String,
    pub result: Option<A2ATaskResult>,
    pub error: Option<JsonRpcError>,
    pub id: String,
}

#[derive(Debug, Deserialize)]
pub struct JsonRpcError {
    pub code: i64,
    pub message: String,
    pub data: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct A2ATaskResult {
    pub id: String,
    pub status: A2ATaskStatus,
    #[serde(default)]
    pub artifacts: Vec<A2AArtifact>,
    #[serde(default)]
    pub metadata: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct A2ATaskStatus {
    pub state: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<A2AMessage>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct A2AArtifact {
    #[serde(default)]
    pub parts: Vec<A2APart>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
}

/// Agent card as returned by /.well-known/agent.json
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentCard {
    pub name: String,
    #[serde(default)]
    pub description: String,
    pub url: String,
    #[serde(default)]
    pub version: String,
    #[serde(default)]
    pub capabilities: Value,
    #[serde(default)]
    pub skills: Vec<AgentSkill>,
    #[serde(default, rename = "securitySchemes")]
    pub security_schemes: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentSkill {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub description: String,
}

impl A2AClient {
    pub fn new(timeout: Duration) -> Self {
        let http = Client::builder()
            .timeout(timeout)
            .build()
            .expect("failed to build HTTP client");
        Self { http }
    }

    /// Discover an agent by fetching its Agent Card from /.well-known/agent.json.
    pub async fn discover(&self, agent_url: &str) -> Result<AgentCard> {
        let url = format!("{}/.well-known/agent.json", agent_url.trim_end_matches('/'));
        let resp = self
            .http
            .get(&url)
            .send()
            .await
            .with_context(|| format!("failed to fetch agent card from {}", url))?;

        if !resp.status().is_success() {
            return Err(anyhow!(
                "agent card request failed: {} {}",
                resp.status(),
                resp.text().await.unwrap_or_default()
            ));
        }

        let card: AgentCard = resp
            .json()
            .await
            .with_context(|| "failed to parse agent card JSON")?;

        Ok(card)
    }

    /// Send a task to an agent using A2A tasks/send.
    pub async fn send_task(
        &self,
        agent_url: &str,
        jwt_token: &str,
        task_id: &str,
        message_text: &str,
        metadata: Option<Value>,
    ) -> Result<A2ATaskResult> {
        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            method: "tasks/send".to_string(),
            params: TaskSendParams {
                id: task_id.to_string(),
                message: A2AMessage {
                    role: "user".to_string(),
                    parts: vec![A2APart::Text {
                        text: message_text.to_string(),
                    }],
                },
                metadata,
            },
            id: format!("req-{}", Uuid::new_v4()),
        };

        let resp = self
            .http
            .post(agent_url)
            .header("Authorization", format!("Bearer {}", jwt_token))
            .header("Content-Type", "application/json")
            .json(&request)
            .send()
            .await
            .with_context(|| format!("failed to send task to agent at {}", agent_url))?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(anyhow!(
                "A2A request failed: {} {}",
                status,
                body
            ));
        }

        let rpc_response: JsonRpcResponse = resp
            .json()
            .await
            .with_context(|| "failed to parse A2A JSON-RPC response")?;

        if let Some(err) = rpc_response.error {
            return Err(anyhow!(
                "A2A JSON-RPC error {}: {}",
                err.code,
                err.message
            ));
        }

        rpc_response
            .result
            .ok_or_else(|| anyhow!("A2A response missing result"))
    }

    /// Check agent health by calling GET {url}/health.
    pub async fn check_health(&self, agent_url: &str, timeout: Duration) -> Result<bool> {
        let url = format!("{}/health", agent_url.trim_end_matches('/'));
        let client = Client::builder()
            .timeout(timeout)
            .build()
            .expect("failed to build health check client");

        match client.get(&url).send().await {
            Ok(resp) => Ok(resp.status().is_success()),
            Err(_) => Ok(false),
        }
    }
}

/// Extract text from A2A artifacts.
pub fn extract_artifact_text(artifacts: &[A2AArtifact]) -> Option<String> {
    for artifact in artifacts {
        for part in &artifact.parts {
            match part {
                A2APart::Text { text } => return Some(text.clone()),
            }
        }
    }
    None
}

/// Extract text from an A2A task result (from artifacts or status message).
pub fn extract_result_text(result: &A2ATaskResult) -> Option<String> {
    // Try artifacts first
    if let Some(text) = extract_artifact_text(&result.artifacts) {
        return Some(text);
    }
    // Fall back to status message
    if let Some(msg) = &result.status.message {
        for part in &msg.parts {
            match part {
                A2APart::Text { text } => return Some(text.clone()),
            }
        }
    }
    None
}
