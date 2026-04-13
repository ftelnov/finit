use async_trait::async_trait;
use bytes::Bytes;
use pingora::http::ResponseHeader;
use pingora::proxy::{ProxyHttp, Session};
use pingora::upstreams::peer::HttpPeer;
use pingora::Error as PingoraError;
use pingora::Result;
use serde_json::json;
use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Instant;

use crate::auth;
use crate::cache::LlmCache;
use crate::guardrails::Guardrails;
use crate::management;
use crate::metrics;
use crate::provider::{ProviderPool, ProviderState};

/// Per-request context stored across Pingora filter phases
pub struct RequestCtx {
    /// The parsed model name from the request body
    pub model: Option<String>,
    /// The task ID from X-Task-ID header
    pub task_id: Option<String>,
    /// The full request body bytes (buffered for forwarding)
    pub request_body: Vec<u8>,
    /// The provider selected for this request
    pub selected_provider: Option<Arc<ProviderState>>,
    /// When the request started
    pub start_time: Instant,
    /// Whether the response was already handled (management endpoints, errors)
    pub response_handled: bool,
    /// Whether this is a streaming request
    pub is_streaming: bool,
    /// Prompt hash for cache-aware routing
    pub prompt_hash: Option<u64>,
    /// Agent ID from X-Agent-ID header
    pub agent_id: Option<String>,
    /// SHA256 cache key for exact-match lookup
    pub cache_hash: Option<String>,
    /// Unique request ID for audit logging
    pub request_id: String,
    /// Time-to-first-token in milliseconds (streaming only)
    pub ttft_ms: Option<u64>,
    /// Prompt version from X-Prompt-Version header (for A/B tracking)
    pub prompt_version: Option<String>,
}

/// The main LLM Router proxy implementation
pub struct LlmRouterProxy {
    pub pool: Arc<ProviderPool>,
    pub http_client: reqwest::Client,
    pub db_url: Option<String>,
    pub db_pool: tokio::sync::OnceCell<sqlx::PgPool>,
    pub cache: LlmCache,
    pub cache_enabled: bool,
    pub guardrails: Arc<Guardrails>,
}

impl LlmRouterProxy {
    /// Get or lazily create the database pool (within Pingora's tokio runtime).
    async fn get_db(&self) -> Option<&sqlx::PgPool> {
        let db_url = self.db_url.as_ref()?;
        let pool = self.db_pool.get_or_try_init(|| async {
            sqlx::postgres::PgPoolOptions::new()
                .max_connections(5)
                .acquire_timeout(std::time::Duration::from_secs(3))
                .connect(db_url)
                .await
        }).await.ok()?;
        Some(pool)
    }
}

#[async_trait]
impl ProxyHttp for LlmRouterProxy {
    type CTX = RequestCtx;

    fn new_ctx(&self) -> Self::CTX {
        RequestCtx {
            model: None,
            task_id: None,
            request_body: Vec::new(),
            selected_provider: None,
            start_time: Instant::now(),
            response_handled: false,
            is_streaming: false,
            prompt_hash: None,
            agent_id: None,
            cache_hash: None,
            request_id: uuid::Uuid::new_v4().to_string(),
            ttft_ms: None,
            prompt_version: None,
        }
    }

    /// Handle incoming requests: management endpoints are served directly,
    /// chat completions are forwarded to upstream providers via reqwest.
    async fn request_filter(&self, session: &mut Session, ctx: &mut Self::CTX) -> Result<bool>
    where
        Self::CTX: Send + Sync,
    {
        let path = session.req_header().uri.path().to_string();
        let method = session.req_header().method.clone();

        // --- Management endpoints (no proxy needed) ---

        if method == http::Method::GET && path == "/health" {
            management::handle_health(session, &self.pool).await?;
            ctx.response_handled = true;
            return Ok(true);
        }

        if method == http::Method::GET && path == "/metrics" {
            management::handle_metrics(session).await?;
            ctx.response_handled = true;
            return Ok(true);
        }

        if method == http::Method::GET && path == "/v1/providers" {
            management::handle_list_providers(session, &self.pool).await?;
            ctx.response_handled = true;
            return Ok(true);
        }

        if method == http::Method::GET && path == "/v1/usage" {
            management::handle_usage(session, &self.pool).await?;
            ctx.response_handled = true;
            return Ok(true);
        }

        // POST /v1/providers - register a new provider
        if method == http::Method::POST && path == "/v1/providers" {
            let body = read_full_body(session).await?;
            management::handle_register_provider(session, &self.pool, &body).await?;
            ctx.response_handled = true;
            return Ok(true);
        }

        // DELETE /v1/cache or DELETE /v1/cache?model=X
        if method == http::Method::DELETE && path == "/v1/cache" {
            if let Some(db) = self.get_db().await {
                let model_filter = session
                    .req_header()
                    .uri
                    .query()
                    .and_then(|q| {
                        q.split('&')
                            .find_map(|pair| pair.strip_prefix("model="))
                            .map(|s| s.to_string())
                    });
                match self.cache.flush(db, model_filter.as_deref()).await {
                    Ok(deleted) => {
                        let body = json!({"message": "cache flushed", "deleted": deleted});
                        management::write_json_response(session, 200, &body).await?;
                    }
                    Err(e) => {
                        let body = json!({"error": {"message": format!("cache flush failed: {}", e), "type": "server_error"}});
                        management::write_json_response(session, 500, &body).await?;
                    }
                }
            } else {
                let body = json!({"error": {"message": "database not configured", "type": "server_error"}});
                management::write_json_response(session, 503, &body).await?;
            }
            ctx.response_handled = true;
            return Ok(true);
        }

        // DELETE /v1/providers/{id}
        if method == http::Method::DELETE && path.starts_with("/v1/providers/") {
            let provider_id = path.trim_start_matches("/v1/providers/");
            management::handle_delete_provider(session, &self.pool, provider_id).await?;
            ctx.response_handled = true;
            return Ok(true);
        }

        // --- Chat completions endpoint ---

        if method == http::Method::POST && path == "/v1/chat/completions" {
            // 1. Auth check
            let auth_header = session
                .req_header()
                .headers
                .get("authorization")
                .and_then(|v| v.to_str().ok())
                .map(|s| s.to_string());

            if let Err(msg) = auth::validate_auth(auth_header.as_deref()) {
                let body = json!({
                    "error": { "message": msg, "type": "auth_error" }
                });
                management::write_json_response(session, 401, &body).await?;
                ctx.response_handled = true;
                return Ok(true);
            }

            // 2. Extract task ID
            ctx.task_id = session
                .req_header()
                .headers
                .get("x-task-id")
                .and_then(|v| v.to_str().ok())
                .map(|s| s.to_string());

            // 3. Read the full request body
            ctx.request_body = read_full_body(session).await?;

            // 4. Parse the request to extract model name and streaming flag
            let parsed: serde_json::Value = match serde_json::from_slice(&ctx.request_body) {
                Ok(v) => v,
                Err(e) => {
                    let body = json!({
                        "error": {
                            "message": format!("invalid JSON body: {}", e),
                            "type": "invalid_request"
                        }
                    });
                    management::write_json_response(session, 400, &body).await?;
                    ctx.response_handled = true;
                    return Ok(true);
                }
            };

            let model = parsed["model"]
                .as_str()
                .unwrap_or("")
                .to_string();

            if model.is_empty() {
                let body = json!({
                    "error": {
                        "message": "missing 'model' field in request body",
                        "type": "invalid_request"
                    }
                });
                management::write_json_response(session, 400, &body).await?;
                ctx.response_handled = true;
                return Ok(true);
            }

            ctx.model = Some(model.clone());
            ctx.is_streaming = parsed["stream"].as_bool().unwrap_or(false);

            // Extract agent ID
            ctx.agent_id = session
                .req_header()
                .headers
                .get("x-agent-id")
                .and_then(|v| v.to_str().ok())
                .map(|s| s.to_string());

            // Extract prompt version for A/B tracking
            ctx.prompt_version = session
                .req_header()
                .headers
                .get("x-prompt-version")
                .and_then(|v| v.to_str().ok())
                .map(|s| s.to_string());

            // Compute prompt hash for cache-aware routing
            ctx.prompt_hash = compute_prompt_hash(&parsed);

            // 4b. Guardrails check
            let guardrail_result = self.guardrails.check_request(&parsed["messages"]);
            if guardrail_result.blocked {
                let violation_type = guardrail_result.violation_type.as_deref().unwrap_or("unknown");
                let reason = guardrail_result.reason.as_deref().unwrap_or("request blocked by guardrails");
                tracing::warn!(
                    violation_type = violation_type,
                    reason = reason,
                    model = %model,
                    "guardrail blocked request"
                );
                metrics::LLM_GUARDRAIL_BLOCKS
                    .with_label_values(&[violation_type])
                    .inc();
                let body = json!({
                    "error": {
                        "message": reason,
                        "type": "guardrail_violation",
                        "violation_type": violation_type
                    }
                });
                management::write_json_response(session, 403, &body).await?;
                ctx.response_handled = true;
                return Ok(true);
            }

            // 5a. Cache lookup (non-streaming only, skip if X-No-Cache header)
            let no_cache = session
                .req_header()
                .headers
                .get("x-no-cache")
                .is_some();

            if self.cache_enabled && !ctx.is_streaming && !no_cache {
                let agent_id = ctx.agent_id.as_deref().unwrap_or("unknown");
                let cache_hash =
                    LlmCache::compute_hash(&model, agent_id, &parsed["messages"]);
                ctx.cache_hash = Some(cache_hash.clone());

                if let Some(db) = self.get_db().await {
                    if let Some(cached) = self.cache.lookup(db, &model, agent_id, &cache_hash).await {
                        tracing::info!(model = %model, agent = agent_id, "cache hit");
                        metrics::LLM_CACHE_HITS.inc();

                        let body_bytes = serde_json::to_vec(&cached.response).unwrap_or_default();
                        let mut resp = ResponseHeader::build(200, None)?;
                        resp.insert_header("Content-Type", "application/json")?;
                        resp.insert_header("Content-Length", body_bytes.len().to_string())?;
                        resp.insert_header("X-Cache", "HIT")?;
                        session
                            .write_response_header(Box::new(resp), false)
                            .await?;
                        session
                            .write_response_body(Some(Bytes::from(body_bytes)), true)
                            .await?;
                        ctx.response_handled = true;
                        return Ok(true);
                    }
                    metrics::LLM_CACHE_MISSES.inc();
                }
            }

            // 5b. Budget check (if DB is configured and task_id is present)
            if let (Some(db), Some(task_id)) = (self.get_db().await, &ctx.task_id) {
                match check_budget(db, task_id).await {
                    Ok(false) => {
                        let body = json!({
                            "error": {
                                "message": "task budget exhausted",
                                "type": "rate_limit"
                            }
                        });
                        management::write_json_response(session, 429, &body).await?;
                        ctx.response_handled = true;
                        return Ok(true);
                    }
                    Ok(true) => {} // Budget available
                    Err(e) => {
                        tracing::warn!(error = %e, "budget check failed, allowing request");
                    }
                }
            }

            // 6. If streaming, add stream_options to get usage in the final chunk
            if ctx.is_streaming {
                if let Ok(mut parsed_mut) = serde_json::from_slice::<serde_json::Value>(&ctx.request_body) {
                    parsed_mut["stream_options"] = json!({"include_usage": true});
                    if let Ok(new_body) = serde_json::to_vec(&parsed_mut) {
                        ctx.request_body = new_body;
                    }
                }
            }

            // 7. Select provider and forward request
            let result = self.forward_to_provider(session, ctx).await;
            if let Err(e) = result {
                tracing::error!(error = %e, "failed to forward request to provider");
                if !ctx.response_handled {
                    let body = json!({
                        "error": {
                            "message": "internal server error",
                            "type": "server_error"
                        }
                    });
                    let _ = management::write_json_response(session, 500, &body).await;
                }
            }
            ctx.response_handled = true;
            return Ok(true);
        }

        // --- Unknown endpoint ---
        let body = json!({
            "error": {
                "message": format!("unknown endpoint: {} {}", method, path),
                "type": "invalid_request"
            }
        });
        management::write_json_response(session, 404, &body).await?;
        ctx.response_handled = true;
        Ok(true)
    }

    /// This is required by ProxyHttp but we handle everything in request_filter.
    /// Returning an error because this should never be reached.
    async fn upstream_peer(
        &self,
        _session: &mut Session,
        _ctx: &mut Self::CTX,
    ) -> Result<Box<HttpPeer>> {
        // This should never be reached because request_filter always returns Ok(true)
        Err(PingoraError::new(pingora::ErrorType::ConnectError))
    }

    async fn logging(&self, session: &mut Session, _e: Option<&PingoraError>, ctx: &mut Self::CTX)
    where
        Self::CTX: Send + Sync,
    {
        let elapsed = ctx.start_time.elapsed();
        let status = session
            .response_written()
            .map_or(0, |r| r.status.as_u16());

        if let Some(provider) = &ctx.selected_provider {
            let model = ctx.model.as_deref().unwrap_or("unknown");

            metrics::LLM_REQUEST_DURATION
                .with_label_values(&[&provider.name, model])
                .observe(elapsed.as_secs_f64());

            metrics::LLM_REQUESTS_TOTAL
                .with_label_values(&[&provider.name, model, &status.to_string()])
                .inc();
        }

        tracing::info!(
            path = %session.req_header().uri.path(),
            status = status,
            duration_ms = elapsed.as_millis() as u64,
            model = ctx.model.as_deref().unwrap_or(""),
            provider = ctx.selected_provider.as_ref().map(|p| p.name.as_str()).unwrap_or(""),
            "request completed"
        );
    }
}

impl LlmRouterProxy {
    /// Forward a chat completion request to a provider, with failover support.
    async fn forward_to_provider(
        &self,
        session: &mut Session,
        ctx: &mut RequestCtx,
    ) -> anyhow::Result<()> {
        let model = ctx.model.clone().unwrap_or_default();

        // Select primary provider
        let provider = match self.pool.select_provider(&model, ctx.prompt_hash) {
            Some(p) => p,
            None => {
                let body = json!({
                    "error": {
                        "message": "no healthy providers available for this model",
                        "type": "service_unavailable"
                    }
                });
                management::write_json_response(session, 503, &body).await?;
                ctx.response_handled = true;
                return Ok(());
            }
        };

        ctx.selected_provider = Some(provider.clone());

        // Try primary provider
        match self.try_provider(session, ctx, &provider).await {
            Ok(()) => return Ok(()),
            Err(e) => {
                tracing::warn!(
                    provider = %provider.name,
                    error = %e,
                    "primary provider failed, attempting failover"
                );
                provider.circuit_breaker.record_failure();

                // Attempt failover to a different provider
                if let Some(failover) = self.pool.select_failover(&model, &provider.name) {
                    tracing::info!(
                        primary = %provider.name,
                        failover = %failover.name,
                        "failing over to alternative provider"
                    );
                    ctx.selected_provider = Some(failover.clone());
                    match self.try_provider(session, ctx, &failover).await {
                        Ok(()) => return Ok(()),
                        Err(e2) => {
                            tracing::error!(
                                provider = %failover.name,
                                error = %e2,
                                "failover provider also failed"
                            );
                            failover.circuit_breaker.record_failure();

                            let body = json!({
                                "error": {
                                    "message": "all providers unavailable",
                                    "type": "service_unavailable"
                                }
                            });
                            management::write_json_response(session, 503, &body).await?;
                            ctx.response_handled = true;
                            return Ok(());
                        }
                    }
                } else {
                    let body = json!({
                        "error": {
                            "message": "no healthy providers available for failover",
                            "type": "service_unavailable"
                        }
                    });
                    management::write_json_response(session, 503, &body).await?;
                    ctx.response_handled = true;
                    return Ok(());
                }
            }
        }
    }

    /// Try sending the request to a specific provider using reqwest.
    /// On success, streams the response back to the client.
    /// On failure (connection error, 5xx), returns Err for failover.
    async fn try_provider(
        &self,
        session: &mut Session,
        ctx: &mut RequestCtx,
        provider: &ProviderState,
    ) -> anyhow::Result<()> {
        // Track active requests
        provider.active_requests.fetch_add(1, Ordering::Relaxed);
        metrics::LLM_ACTIVE_REQUESTS
            .with_label_values(&[&provider.name])
            .inc();

        let request_start = Instant::now();
        let url = provider.chat_completions_url();
        let result = self.do_provider_call(session, ctx, provider, &url).await;

        // Decrement active requests
        provider.active_requests.fetch_sub(1, Ordering::Relaxed);
        metrics::LLM_ACTIVE_REQUESTS
            .with_label_values(&[&provider.name])
            .dec();

        let latency_ms = request_start.elapsed().as_millis() as u64;

        match &result {
            Ok(()) => {
                provider.total_requests.fetch_add(1, Ordering::Relaxed);
                provider.update_ewma_latency(latency_ms as f64);
                provider.circuit_breaker.record_success();

                metrics::LLM_PROVIDER_LATENCY_EWMA
                    .with_label_values(&[&provider.name])
                    .set(provider.ewma_latency_ms() / 1000.0);
            }
            Err(e) => {
                let model = ctx.model.as_deref().unwrap_or("unknown");
                tracing::warn!(
                    provider = %provider.name,
                    model = model,
                    latency_ms = latency_ms,
                    error = %e,
                    "provider call failed"
                );
            }
        }

        result
    }

    /// Actually make the HTTP call to the provider and stream the response back.
    async fn do_provider_call(
        &self,
        session: &mut Session,
        ctx: &mut RequestCtx,
        provider: &ProviderState,
        url: &str,
    ) -> anyhow::Result<()> {
        let mut req_builder = self
            .http_client
            .post(url)
            .header("Content-Type", "application/json")
            .timeout(provider.timeout)
            .body(ctx.request_body.clone());

        // Add the provider's API key if present
        if let Some(api_key) = &provider.api_key {
            req_builder = req_builder.header("Authorization", format!("Bearer {}", api_key));
        }

        let response = req_builder.send().await.map_err(|e| {
            anyhow::anyhow!("connection error to provider {}: {}", provider.name, e)
        })?;

        let status = response.status();

        // If the provider returns 5xx, return error for failover
        if status.is_server_error() {
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow::anyhow!(
                "provider {} returned {}: {}",
                provider.name,
                status,
                body.chars().take(200).collect::<String>()
            ));
        }

        // Forward the response status and headers to the client
        let resp_status = status.as_u16();
        let mut resp_header = ResponseHeader::build(resp_status, None)?;

        // Copy relevant headers from upstream response
        if let Some(ct) = response.headers().get("content-type") {
            resp_header.insert_header("Content-Type", ct.to_str().unwrap_or("application/json"))?;
        } else {
            resp_header.insert_header("Content-Type", "application/json")?;
        }

        // For streaming responses, use chunked transfer encoding
        if ctx.is_streaming {
            resp_header.insert_header("Transfer-Encoding", "chunked")?;
            resp_header.insert_header("Cache-Control", "no-cache")?;

            session
                .write_response_header(Box::new(resp_header), false)
                .await?;

            // Stream chunks from upstream to downstream
            let mut stream = response.bytes_stream();
            let mut total_chunks: u64 = 0;
            let mut usage_data: Option<serde_json::Value> = None;

            use futures_util::StreamExt;
            while let Some(chunk_result) = stream.next().await {
                match chunk_result {
                    Ok(chunk) => {
                        total_chunks += 1;

                        // Track time-to-first-token
                        if total_chunks == 1 {
                            ctx.ttft_ms = Some(ctx.start_time.elapsed().as_millis() as u64);
                        }

                        // Try to extract usage from the chunk (final SSE chunk)
                        if let Some(usage) = extract_usage_from_sse_chunk(&chunk) {
                            usage_data = Some(usage);
                        }

                        session
                            .write_response_body(Some(chunk), false)
                            .await?;
                    }
                    Err(e) => {
                        tracing::warn!(error = %e, "error reading streaming chunk from provider");
                        break;
                    }
                }
            }

            // End the response
            session.write_response_body(None, true).await?;

            // Record token metrics from usage data
            self.record_usage(provider, ctx, usage_data, total_chunks, resp_status).await;
        } else {
            // Non-streaming: read the full response body
            let body_bytes = response.bytes().await.map_err(|e| {
                anyhow::anyhow!("error reading response body from provider: {}", e)
            })?;

            // Parse for usage data and caching
            let parsed_response = serde_json::from_slice::<serde_json::Value>(&body_bytes).ok();
            let usage_data = parsed_response.as_ref().and_then(|v| v.get("usage").cloned());

            resp_header.insert_header("Content-Length", body_bytes.len().to_string())?;
            resp_header.insert_header("X-Cache", "MISS")?;
            session
                .write_response_header(Box::new(resp_header), false)
                .await?;
            session
                .write_response_body(Some(body_bytes), true)
                .await?;

            // Record token metrics
            self.record_usage(provider, ctx, usage_data.clone(), 0, resp_status).await;

            // Store in cache (non-streaming, successful, DB available)
            if self.cache_enabled && status.is_success() {
                if let (Some(db), Some(hash), Some(resp_val)) =
                    (self.get_db().await, &ctx.cache_hash, &parsed_response)
                {
                    let agent_id = ctx.agent_id.as_deref().unwrap_or("unknown");
                    let model = ctx.model.as_deref().unwrap_or("unknown");
                    let tokens = usage_data
                        .as_ref()
                        .and_then(|u| u["total_tokens"].as_i64())
                        .unwrap_or(0) as i32;
                    self.cache
                        .store(db, model, agent_id, hash, resp_val, tokens)
                        .await;
                }
            }
        }

        ctx.response_handled = true;
        Ok(())
    }

    /// Record token usage metrics and optionally update the database
    async fn record_usage(
        &self,
        provider: &ProviderState,
        ctx: &RequestCtx,
        usage: Option<serde_json::Value>,
        fallback_chunks: u64,
        status_code: u16,
    ) {
        let model = ctx.model.as_deref().unwrap_or("unknown");

        let (input_tokens, output_tokens) = if let Some(usage) = &usage {
            let input = usage["prompt_tokens"].as_u64().unwrap_or(0);
            let output = usage["completion_tokens"].as_u64().unwrap_or(0);
            (input, output)
        } else if fallback_chunks > 0 {
            // Fallback: estimate tokens from chunk count
            // Rough estimate: ~4 tokens per chunk on average
            (0, fallback_chunks * 4)
        } else {
            (0, 0)
        };

        if input_tokens > 0 {
            metrics::LLM_TOKENS_TOTAL
                .with_label_values(&[&provider.name, model, "input"])
                .inc_by(input_tokens as f64);
        }
        if output_tokens > 0 {
            metrics::LLM_TOKENS_TOTAL
                .with_label_values(&[&provider.name, model, "output"])
                .inc_by(output_tokens as f64);
        }

        // Calculate cost
        let cost = if let Some(model_config) = provider.models.get(model) {
            let input_cost =
                (input_tokens as f64 / 1_000_000.0) * model_config.pricing.input_per_1m;
            let output_cost =
                (output_tokens as f64 / 1_000_000.0) * model_config.pricing.output_per_1m;
            input_cost + output_cost
        } else {
            0.0
        };

        // Update budget in database if configured
        if let (Some(db), Some(task_id)) = (self.get_db().await, &ctx.task_id) {
            let total_tokens = (input_tokens + output_tokens) as i64;
            if let Err(e) = update_budget(db, task_id, total_tokens, cost).await {
                tracing::warn!(error = %e, task_id = %task_id, "failed to update budget");
            }
        }

        // Insert audit row into llm_usage table
        if let Some(db) = self.get_db().await {
            let result = sqlx::query(
                "INSERT INTO llm_usage (request_id, task_id, agent_id, provider_id, model, input_tokens, output_tokens, cost_dollars, ttft_ms, total_latency_ms, status, prompt_version)
                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)"
            )
            .bind(&ctx.request_id)
            .bind(ctx.task_id.as_deref())
            .bind(ctx.agent_id.as_deref())
            .bind(&provider.name)
            .bind(model)
            .bind(input_tokens as i32)
            .bind(output_tokens as i32)
            .bind(cost)
            .bind(ctx.ttft_ms.map(|v| v as i32))
            .bind(ctx.start_time.elapsed().as_millis() as i32)
            .bind(status_code as i32)
            .bind(ctx.prompt_version.as_deref())
            .execute(db)
            .await;
            if let Err(e) = result {
                tracing::warn!(error = %e, "failed to log llm_usage");
            }
        }

        tracing::debug!(
            provider = %provider.name,
            model = model,
            input_tokens = input_tokens,
            output_tokens = output_tokens,
            cost = cost,
            "recorded usage"
        );
    }
}

/// Read the full request body from the session
async fn read_full_body(session: &mut Session) -> Result<Vec<u8>> {
    let mut body = Vec::new();
    loop {
        match session.read_request_body().await? {
            Some(bytes) => body.extend_from_slice(&bytes),
            None => break,
        }
    }
    Ok(body)
}

/// Compute a hash of the system prompt for cache-aware routing
fn compute_prompt_hash(request: &serde_json::Value) -> Option<u64> {
    let messages = request["messages"].as_array()?;

    // Find the system message
    let system_content = messages.iter().find_map(|msg| {
        if msg["role"].as_str() == Some("system") {
            msg["content"].as_str()
        } else {
            None
        }
    });

    // Also include first N characters of the first user message
    let user_prefix = messages.iter().find_map(|msg| {
        if msg["role"].as_str() == Some("user") {
            msg["content"].as_str().map(|s| {
                let end = s.len().min(200);
                &s[..end]
            })
        } else {
            None
        }
    });

    if system_content.is_none() && user_prefix.is_none() {
        return None;
    }

    let mut hasher = DefaultHasher::new();
    if let Some(sys) = system_content {
        sys.hash(&mut hasher);
    }
    if let Some(usr) = user_prefix {
        usr.hash(&mut hasher);
    }
    Some(hasher.finish())
}

/// Extract usage data from an SSE streaming chunk.
/// The usage typically appears in the final `data: {...}` line.
fn extract_usage_from_sse_chunk(chunk: &Bytes) -> Option<serde_json::Value> {
    let text = std::str::from_utf8(chunk).ok()?;

    for line in text.lines() {
        let Some(data) = line.strip_prefix("data: ") else {
            continue;
        };
        if data == "[DONE]" {
            continue;
        }
        if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(data) {
            if parsed.get("usage").is_some() && !parsed["usage"].is_null() {
                return parsed.get("usage").cloned();
            }
        }
    }

    None
}

/// Check if the task has remaining budget
async fn check_budget(db: &sqlx::PgPool, task_id: &str) -> anyhow::Result<bool> {
    let row = sqlx::query_as::<_, (i32, i32)>(
        "SELECT spent_tokens, max_tokens FROM task_budgets WHERE task_id = $1",
    )
    .bind(task_id)
    .fetch_optional(db)
    .await?;

    match row {
        Some((spent, max_tokens)) => Ok(spent < max_tokens),
        None => Ok(true), // No budget row means no limit
    }
}

/// Update the budget after a successful request.
/// Only UPDATE existing rows — the orchestrator creates the budget row at task creation.
async fn update_budget(
    db: &sqlx::PgPool,
    task_id: &str,
    tokens: i64,
    cost: f64,
) -> anyhow::Result<()> {
    sqlx::query(
        "UPDATE task_budgets
         SET spent_tokens = spent_tokens + $2,
             spent_cost = spent_cost + $3
         WHERE task_id = $1",
    )
    .bind(task_id)
    .bind(tokens as i32)
    .bind(cost)
    .execute(db)
    .await?;
    Ok(())
}
