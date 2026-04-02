use serde::Deserialize;
use std::collections::HashMap;

#[derive(Debug, Clone, Deserialize)]
pub struct RouterConfig {
    pub listen: String,
    #[serde(default)]
    pub providers: Vec<ProviderConfig>,
    #[serde(default)]
    pub balancing: BalancingConfig,
    #[serde(default)]
    pub circuit_breaker: CircuitBreakerConfig,
    #[serde(default)]
    pub budget: BudgetConfig,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ProviderConfig {
    pub name: String,
    pub url: String,
    #[serde(default)]
    pub api_key: Option<String>,
    #[serde(default = "default_weight")]
    pub weight: u32,
    #[serde(default)]
    pub models: HashMap<String, ModelConfig>,
    #[serde(default = "default_timeout_ms")]
    pub timeout_ms: u64,
    #[serde(default = "default_health_check_interval")]
    pub health_check_interval_s: u64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ModelConfig {
    #[serde(default)]
    pub pricing: PricingConfig,
    #[serde(default = "default_context_window")]
    pub context_window: u64,
    #[serde(default = "default_max_output_tokens")]
    pub max_output_tokens: u64,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct PricingConfig {
    #[serde(default)]
    pub input_per_1m: f64,
    #[serde(default)]
    pub output_per_1m: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct BalancingConfig {
    #[serde(default = "default_strategy")]
    pub strategy: String,
    #[serde(default = "default_true")]
    pub health_aware: bool,
}

impl Default for BalancingConfig {
    fn default() -> Self {
        Self {
            strategy: "round-robin".to_string(),
            health_aware: true,
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct CircuitBreakerConfig {
    #[serde(default = "default_failure_threshold")]
    pub failure_threshold: u32,
    #[serde(default = "default_failure_window")]
    pub failure_window_s: u64,
    #[serde(default = "default_cooldown")]
    pub cooldown_s: u64,
    #[serde(default = "default_half_open_requests")]
    pub half_open_requests: u32,
}

impl Default for CircuitBreakerConfig {
    fn default() -> Self {
        Self {
            failure_threshold: 3,
            failure_window_s: 30,
            cooldown_s: 30,
            half_open_requests: 1,
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct BudgetConfig {
    #[serde(default = "default_max_tokens")]
    pub default_max_tokens: u64,
    #[serde(default = "default_max_calls")]
    pub default_max_calls: u64,
}

impl Default for BudgetConfig {
    fn default() -> Self {
        Self {
            default_max_tokens: 500_000,
            default_max_calls: 50,
        }
    }
}

fn default_weight() -> u32 {
    1
}
fn default_timeout_ms() -> u64 {
    30_000
}
fn default_health_check_interval() -> u64 {
    10
}
fn default_context_window() -> u64 {
    32_768
}
fn default_max_output_tokens() -> u64 {
    8_192
}
fn default_strategy() -> String {
    "round-robin".to_string()
}
fn default_true() -> bool {
    true
}
fn default_failure_threshold() -> u32 {
    3
}
fn default_failure_window() -> u64 {
    30
}
fn default_cooldown() -> u64 {
    30
}
fn default_half_open_requests() -> u32 {
    1
}
fn default_max_tokens() -> u64 {
    500_000
}
fn default_max_calls() -> u64 {
    50
}

impl RouterConfig {
    pub fn load(path: &str) -> anyhow::Result<Self> {
        let content = std::fs::read_to_string(path)?;
        // Expand environment variables in the YAML content
        let expanded = expand_env_vars(&content);
        let config: RouterConfig = serde_yaml::from_str(&expanded)?;
        Ok(config)
    }
}

/// Simple environment variable expansion: replaces ${VAR} with the value of VAR
fn expand_env_vars(input: &str) -> String {
    let mut result = input.to_string();
    let mut start = 0;
    while let Some(pos) = result[start..].find("${") {
        let abs_pos = start + pos;
        if let Some(end_pos) = result[abs_pos..].find('}') {
            let abs_end = abs_pos + end_pos;
            let var_name = &result[abs_pos + 2..abs_end];
            let replacement = std::env::var(var_name).unwrap_or_default();
            result.replace_range(abs_pos..=abs_end, &replacement);
            start = abs_pos + replacement.len();
        } else {
            break;
        }
    }
    result
}
