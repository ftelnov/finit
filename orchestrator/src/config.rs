use std::env;

/// Application configuration, loaded from environment variables.
#[derive(Debug, Clone)]
pub struct Config {
    pub listen_addr: String,
    pub database_url: String,
    pub jwt_secret: String,
    pub llm_router_url: String,
    pub max_iterations: i32,
    pub max_task_duration_s: i32,
    pub phase_timeout_s: i32,
    pub retry_max: i32,
    pub retry_delay_s: u64,
    pub default_max_tokens: i32,
    pub default_max_calls: i32,
    pub health_check_interval_s: u64,
    pub health_check_timeout_s: u64,
    pub unhealthy_threshold: i32,
    pub max_events_per_task: i32,
    pub db_max_connections: u32,
    pub supervisor_model: String,
}

impl Config {
    /// Load configuration from environment variables with sensible defaults.
    pub fn from_env() -> Self {
        Self {
            listen_addr: env::var("LISTEN_ADDR").unwrap_or_else(|_| "0.0.0.0:8080".into()),
            database_url: env::var("DATABASE_URL")
                .expect("DATABASE_URL must be set"),
            jwt_secret: env::var("JWT_SECRET").unwrap_or_else(|_| "dev-secret".into()),
            llm_router_url: env::var("LLM_ROUTER_URL")
                .unwrap_or_else(|_| "http://llm-router:8081".into()),
            max_iterations: env::var("MAX_ITERATIONS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(3),
            max_task_duration_s: env::var("MAX_TASK_DURATION_S")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(1800),
            phase_timeout_s: env::var("PHASE_TIMEOUT_S")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(600),
            retry_max: env::var("RETRY_MAX")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(2),
            retry_delay_s: env::var("RETRY_DELAY_S")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(5),
            default_max_tokens: env::var("DEFAULT_MAX_TOKENS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(500_000),
            default_max_calls: env::var("DEFAULT_MAX_CALLS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(50),
            health_check_interval_s: env::var("HEALTH_CHECK_INTERVAL_S")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(10),
            health_check_timeout_s: env::var("HEALTH_CHECK_TIMEOUT_S")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(5),
            unhealthy_threshold: env::var("UNHEALTHY_THRESHOLD")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(3),
            max_events_per_task: env::var("MAX_EVENTS_PER_TASK")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(10_000),
            db_max_connections: env::var("DB_MAX_CONNECTIONS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(20),
            supervisor_model: env::var("SUPERVISOR_MODEL")
                .or_else(|_| env::var("LLM_MODEL"))
                .unwrap_or_else(|_| "default".into()),
        }
    }
}
