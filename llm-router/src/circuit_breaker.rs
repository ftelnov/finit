use parking_lot::Mutex;
use std::collections::VecDeque;
use std::time::{Duration, Instant};

/// Circuit breaker states
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum CircuitState {
    Closed,
    Open,
    HalfOpen,
}

impl CircuitState {
    /// Returns a numeric value for Prometheus gauge
    pub fn as_f64(&self) -> f64 {
        match self {
            CircuitState::Closed => 0.0,
            CircuitState::Open => 1.0,
            CircuitState::HalfOpen => 0.5,
        }
    }
}

/// Per-provider circuit breaker
pub struct CircuitBreaker {
    inner: Mutex<CircuitBreakerInner>,
}

struct CircuitBreakerInner {
    state: CircuitState,
    failure_threshold: u32,
    failure_window: Duration,
    cooldown: Duration,
    half_open_requests: u32,
    /// Timestamps of recent failures within the failure window
    failures: VecDeque<Instant>,
    /// When the circuit was opened
    opened_at: Option<Instant>,
    /// Number of half-open probe requests currently allowed
    half_open_active: u32,
}

impl CircuitBreaker {
    pub fn new(failure_threshold: u32, failure_window_s: u64, cooldown_s: u64, half_open_requests: u32) -> Self {
        Self {
            inner: Mutex::new(CircuitBreakerInner {
                state: CircuitState::Closed,
                failure_threshold,
                failure_window: Duration::from_secs(failure_window_s),
                cooldown: Duration::from_secs(cooldown_s),
                half_open_requests,
                failures: VecDeque::new(),
                opened_at: None,
                half_open_active: 0,
            }),
        }
    }

    /// Check if a request is allowed through the circuit breaker.
    /// Returns true if the request should proceed.
    pub fn allow_request(&self) -> bool {
        let mut inner = self.inner.lock();
        let now = Instant::now();

        match inner.state {
            CircuitState::Closed => true,
            CircuitState::Open => {
                // Check if cooldown has elapsed
                if let Some(opened_at) = inner.opened_at {
                    if now.duration_since(opened_at) >= inner.cooldown {
                        // Transition to half-open
                        inner.state = CircuitState::HalfOpen;
                        inner.half_open_active = 0;
                        // Allow first probe request
                        if inner.half_open_active < inner.half_open_requests {
                            inner.half_open_active += 1;
                            return true;
                        }
                    }
                }
                false
            }
            CircuitState::HalfOpen => {
                // Only allow limited probe requests
                if inner.half_open_active < inner.half_open_requests {
                    inner.half_open_active += 1;
                    true
                } else {
                    false
                }
            }
        }
    }

    /// Record a successful request
    pub fn record_success(&self) {
        let mut inner = self.inner.lock();
        match inner.state {
            CircuitState::HalfOpen => {
                // Probe succeeded, close the circuit
                inner.state = CircuitState::Closed;
                inner.failures.clear();
                inner.opened_at = None;
                inner.half_open_active = 0;
            }
            CircuitState::Closed => {
                // Success in closed state, no action needed
            }
            CircuitState::Open => {
                // Shouldn't happen but handle gracefully
            }
        }
    }

    /// Record a failed request
    pub fn record_failure(&self) {
        let mut inner = self.inner.lock();
        let now = Instant::now();

        match inner.state {
            CircuitState::Closed => {
                // Add failure timestamp
                inner.failures.push_back(now);

                // Remove old failures outside the window
                let cutoff = now - inner.failure_window;
                while inner.failures.front().is_some_and(|t| *t < cutoff) {
                    inner.failures.pop_front();
                }

                // Check if threshold is exceeded
                if inner.failures.len() as u32 >= inner.failure_threshold {
                    inner.state = CircuitState::Open;
                    inner.opened_at = Some(now);
                    tracing::warn!("Circuit breaker opened after {} failures", inner.failures.len());
                }
            }
            CircuitState::HalfOpen => {
                // Probe failed, re-open the circuit
                inner.state = CircuitState::Open;
                inner.opened_at = Some(now);
                inner.half_open_active = 0;
                tracing::warn!("Circuit breaker re-opened after half-open probe failure");
            }
            CircuitState::Open => {
                // Already open, reset cooldown
                inner.opened_at = Some(now);
            }
        }
    }

    /// Get the current state
    pub fn state(&self) -> CircuitState {
        let mut inner = self.inner.lock();
        let now = Instant::now();

        // Auto-transition from open to half-open if cooldown elapsed
        if inner.state == CircuitState::Open {
            if let Some(opened_at) = inner.opened_at {
                if now.duration_since(opened_at) >= inner.cooldown {
                    inner.state = CircuitState::HalfOpen;
                    inner.half_open_active = 0;
                }
            }
        }

        inner.state
    }
}
