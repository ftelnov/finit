use sha2::{Digest, Sha256};
use sqlx::PgPool;
use std::time::Duration;

/// LLM response cache backed by PostgreSQL with two-tier lookup:
///
/// 1. **Exact match** — SHA256 hash of (model, agent_id, messages). O(1) via btree.
/// 2. **Semantic match** — pgvector cosine similarity on message embeddings.
///    Falls back to this when exact miss. Requires embedding to be stored.
///
/// The `llm_cache` table has both `messages_hash` (exact) and `embedding` (semantic)
/// columns with appropriate indexes (btree and IVFFlat respectively).
pub struct LlmCache {
    ttl: Duration,
    /// Cosine similarity threshold for semantic cache hits (0.0–1.0).
    /// Only entries with similarity >= threshold are returned.
    semantic_threshold: f64,
}

#[derive(Debug)]
pub struct CachedResponse {
    pub response: serde_json::Value,
    pub tokens_saved: i32,
}

impl LlmCache {
    pub fn new(ttl_seconds: u64) -> Self {
        Self {
            ttl: Duration::from_secs(ttl_seconds),
            semantic_threshold: 0.95,
        }
    }

    pub fn with_semantic_threshold(mut self, threshold: f64) -> Self {
        self.semantic_threshold = threshold.clamp(0.0, 1.0);
        self
    }

    /// Compute SHA256 hash of model + agent_id + canonical messages content.
    pub fn compute_hash(model: &str, agent_id: &str, messages: &serde_json::Value) -> String {
        let mut hasher = Sha256::new();
        hasher.update(model.as_bytes());
        hasher.update(b"|");
        hasher.update(agent_id.as_bytes());
        hasher.update(b"|");
        if let Some(msgs) = messages.as_array() {
            for msg in msgs {
                let role = msg["role"].as_str().unwrap_or("");
                hasher.update(role.as_bytes());
                hasher.update(b":");
                hasher.update(msg["content"].as_str().unwrap_or("").as_bytes());
                hasher.update(b"\n");
            }
        }
        hex::encode(hasher.finalize())
    }

    /// Look up a cached response. Tries exact SHA256 match first, then falls
    /// back to semantic similarity search via pgvector if an embedding is provided.
    pub async fn lookup(
        &self,
        db: &PgPool,
        model: &str,
        agent_id: &str,
        messages_hash: &str,
    ) -> Option<CachedResponse> {
        self.lookup_exact(db, model, agent_id, messages_hash).await
    }

    /// Two-tier lookup: exact match → semantic fallback.
    /// `embedding` is the 384-dim vector for the user messages.
    pub async fn lookup_with_embedding(
        &self,
        db: &PgPool,
        model: &str,
        agent_id: &str,
        messages_hash: &str,
        embedding: &[f32],
    ) -> Option<CachedResponse> {
        // Tier 1: exact match (fastest)
        if let Some(hit) = self.lookup_exact(db, model, agent_id, messages_hash).await {
            return Some(hit);
        }

        // Tier 2: semantic similarity via pgvector cosine distance
        self.lookup_semantic(db, model, agent_id, embedding).await
    }

    /// Exact-match lookup by SHA256 hash. O(1) via btree index.
    async fn lookup_exact(
        &self,
        db: &PgPool,
        model: &str,
        agent_id: &str,
        messages_hash: &str,
    ) -> Option<CachedResponse> {
        let row = sqlx::query_as::<_, (serde_json::Value, i32)>(
            "SELECT response, tokens_saved FROM llm_cache
             WHERE model = $1 AND agent_id = $2 AND messages_hash = $3
               AND expires_at > NOW()
             LIMIT 1",
        )
        .bind(model)
        .bind(agent_id)
        .bind(messages_hash)
        .fetch_optional(db)
        .await
        .ok()
        .flatten()?;

        Some(CachedResponse {
            response: row.0,
            tokens_saved: row.1,
        })
    }

    /// Semantic similarity lookup via pgvector cosine distance operator (<=>).
    /// Returns the closest match above `semantic_threshold`.
    async fn lookup_semantic(
        &self,
        db: &PgPool,
        model: &str,
        agent_id: &str,
        embedding: &[f32],
    ) -> Option<CachedResponse> {
        // pgvector cosine distance: <=> returns distance (0=identical, 2=opposite),
        // so similarity = 1 - distance. We filter for similarity >= threshold.
        let max_distance = 1.0 - self.semantic_threshold;

        // Format embedding as pgvector literal: '[0.1,0.2,...]'
        let embedding_str = format!(
            "[{}]",
            embedding
                .iter()
                .map(|v| format!("{:.6}", v))
                .collect::<Vec<_>>()
                .join(",")
        );

        let row = sqlx::query_as::<_, (serde_json::Value, i32, f64)>(
            "SELECT response, tokens_saved, (embedding <=> $4::vector) AS distance
             FROM llm_cache
             WHERE model = $1 AND agent_id = $2
               AND embedding IS NOT NULL
               AND expires_at > NOW()
               AND (embedding <=> $4::vector) <= $3
             ORDER BY embedding <=> $4::vector
             LIMIT 1",
        )
        .bind(model)
        .bind(agent_id)
        .bind(max_distance)
        .bind(&embedding_str)
        .fetch_optional(db)
        .await
        .ok()
        .flatten()?;

        let similarity = 1.0 - row.2;
        tracing::info!(
            model = model,
            agent = agent_id,
            similarity = format!("{:.4}", similarity),
            "semantic cache hit"
        );

        Some(CachedResponse {
            response: row.0,
            tokens_saved: row.1,
        })
    }

    /// Store a response in the cache (without embedding — exact-match only).
    pub async fn store(
        &self,
        db: &PgPool,
        model: &str,
        agent_id: &str,
        messages_hash: &str,
        response: &serde_json::Value,
        tokens_saved: i32,
    ) {
        self.store_with_embedding(db, model, agent_id, messages_hash, None, response, tokens_saved)
            .await;
    }

    /// Store a response in the cache with an optional embedding for semantic lookup.
    pub async fn store_with_embedding(
        &self,
        db: &PgPool,
        model: &str,
        agent_id: &str,
        messages_hash: &str,
        embedding: Option<&[f32]>,
        response: &serde_json::Value,
        tokens_saved: i32,
    ) {
        let ttl_secs = self.ttl.as_secs() as f64;
        let embedding_str = embedding.map(|e| {
            format!(
                "[{}]",
                e.iter()
                    .map(|v| format!("{:.6}", v))
                    .collect::<Vec<_>>()
                    .join(",")
            )
        });

        let result = sqlx::query(
            "INSERT INTO llm_cache (model, agent_id, messages_hash, embedding, response, tokens_saved, expires_at)
             VALUES ($1, $2, $3, $7::vector, $4, $5, NOW() + make_interval(secs => $6))
             ON CONFLICT ON CONSTRAINT llm_cache_exact_uq
             DO UPDATE SET response = $4, tokens_saved = $5,
                           embedding = COALESCE($7::vector, llm_cache.embedding),
                           expires_at = NOW() + make_interval(secs => $6)",
        )
        .bind(model)
        .bind(agent_id)
        .bind(messages_hash)
        .bind(response)
        .bind(tokens_saved)
        .bind(ttl_secs)
        .bind(embedding_str.as_deref())
        .execute(db)
        .await;

        if let Err(e) = result {
            tracing::warn!(error = %e, "failed to store cache entry");
        }
    }

    /// Flush cache entries, optionally filtered by model.
    pub async fn flush(&self, db: &PgPool, model: Option<&str>) -> Result<u64, sqlx::Error> {
        let result = if let Some(m) = model {
            sqlx::query("DELETE FROM llm_cache WHERE model = $1")
                .bind(m)
                .execute(db)
                .await?
        } else {
            sqlx::query("DELETE FROM llm_cache").execute(db).await?
        };
        Ok(result.rows_affected())
    }

    /// Remove expired entries.
    pub async fn cleanup_expired(&self, db: &PgPool) -> Result<u64, sqlx::Error> {
        let result = sqlx::query("DELETE FROM llm_cache WHERE expires_at < NOW()")
            .execute(db)
            .await?;
        Ok(result.rows_affected())
    }
}
