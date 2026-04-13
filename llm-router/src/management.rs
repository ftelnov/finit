use crate::provider::{ProviderPool, RegisterProviderRequest};
use bytes::Bytes;
use pingora::http::ResponseHeader;
use pingora::proxy::Session;
use pingora::Result;
use serde_json::json;
use std::sync::Arc;

/// Handle GET /health
pub async fn handle_health(session: &mut Session, pool: &ProviderPool) -> Result<()> {
    let has_healthy = pool.has_healthy_provider();
    let status = 200; // Router is healthy even if providers are down
    let body = json!({
        "status": if has_healthy { "ok" } else { "degraded" },
        "providers_total": pool.providers().len(),
        "providers_healthy": pool.providers().iter().filter(|p| {
            p.healthy.load(std::sync::atomic::Ordering::Relaxed)
        }).count(),
    });

    write_json_response(session, status, &body).await
}

/// Handle GET /metrics
pub async fn handle_metrics(session: &mut Session) -> Result<()> {
    let encoder = prometheus::TextEncoder::new();
    let metric_families = prometheus::gather();
    let output = encoder
        .encode_to_string(&metric_families)
        .unwrap_or_else(|e| format!("# Error encoding metrics: {}\n", e));

    let mut resp = ResponseHeader::build(200, None)?;
    resp.insert_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")?;
    resp.insert_header("Content-Length", output.len().to_string())?;
    session
        .write_response_header(Box::new(resp), false)
        .await?;
    session
        .write_response_body(Some(Bytes::from(output)), true)
        .await?;
    Ok(())
}

/// Handle GET /v1/providers
pub async fn handle_list_providers(
    session: &mut Session,
    pool: &ProviderPool,
) -> Result<()> {
    let providers: Vec<_> = pool.providers().iter().map(|p| p.to_info()).collect();
    let body = json!({ "providers": providers });
    write_json_response(session, 200, &body).await
}

/// Handle POST /v1/providers
pub async fn handle_register_provider(
    session: &mut Session,
    pool: &Arc<ProviderPool>,
    request_body: &[u8],
) -> Result<()> {
    let req: RegisterProviderRequest = match serde_json::from_slice(request_body) {
        Ok(r) => r,
        Err(e) => {
            let body = json!({
                "error": {
                    "message": format!("invalid request body: {}", e),
                    "type": "invalid_request"
                }
            });
            return write_json_response(session, 400, &body).await;
        }
    };

    let provider = pool.register_provider(req);
    let body = json!({
        "provider": provider.to_info(),
        "message": "provider registered"
    });
    write_json_response(session, 201, &body).await
}

/// Handle DELETE /v1/providers/{id}
pub async fn handle_delete_provider(
    session: &mut Session,
    pool: &ProviderPool,
    provider_id: &str,
) -> Result<()> {
    if pool.remove_provider(provider_id) {
        let body = json!({ "message": "provider removed" });
        write_json_response(session, 200, &body).await
    } else {
        let body = json!({
            "error": {
                "message": "provider not found",
                "type": "not_found"
            }
        });
        write_json_response(session, 404, &body).await
    }
}

/// Handle GET /v1/usage
pub async fn handle_usage(
    session: &mut Session,
    pool: &ProviderPool,
) -> Result<()> {
    let providers = pool.providers();
    let total_requests: u64 = providers
        .iter()
        .map(|p| p.total_requests.load(std::sync::atomic::Ordering::Relaxed))
        .sum();

    let body = json!({
        "total_requests": total_requests,
        "providers": providers.iter().map(|p| json!({
            "name": p.name,
            "total_requests": p.total_requests.load(std::sync::atomic::Ordering::Relaxed),
            "active_requests": p.active_requests.load(std::sync::atomic::Ordering::Relaxed),
            "ewma_latency_ms": p.ewma_latency_ms(),
        })).collect::<Vec<_>>(),
    });
    write_json_response(session, 200, &body).await
}

/// Write a JSON response to the session
pub async fn write_json_response(
    session: &mut Session,
    status: u16,
    body: &serde_json::Value,
) -> Result<()> {
    let body_bytes = serde_json::to_vec(body).unwrap_or_default();
    let mut resp = ResponseHeader::build(status, None)?;
    resp.insert_header("Content-Type", "application/json")?;
    resp.insert_header("Content-Length", body_bytes.len().to_string())?;
    session
        .write_response_header(Box::new(resp), false)
        .await?;
    session
        .write_response_body(Some(Bytes::from(body_bytes)), true)
        .await?;
    Ok(())
}
