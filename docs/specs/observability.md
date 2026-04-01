# Спецификация: Observability и Evals

## Назначение

Полная наблюдаемость платформы: метрики, трассировки, логи, оценка качества. Покрывает все уровни: LLM-вызовы, агентные взаимодействия, pipeline performance, инфраструктура.

## Стек

| Компонент | Роль |
|---|---|
| **OpenTelemetry SDK** | Инструментация всех Go/Python сервисов |
| **OTel Collector** | Сбор, обработка, экспорт телеметрии |
| **Prometheus** | Хранение метрик, алерты |
| **Grafana** | Дашборды, визуализация |
| **MLFlow** | Трассировка LLM-вызовов, эксперименты |

## Traces (Distributed Tracing)

### Propagation

W3C Trace Context (`traceparent` header) передается через все hops:

```
User Request
  └── [Orchestrator] trace_id=abc, span="handle_task"
       ├── [A2A call] span="a2a.bootstrapper.prepare_workspace"
       │    └── [LLM Router] span="llm.chat_completion"
       │         └── [Provider] span="provider.vllm.forward"
       ├── [A2A call] span="a2a.worker.develop"
       │    ├── [LLM Router] span="llm.chat_completion" (x3)
       │    └── [Sandbox] span="sandbox.exec.go_test"
       └── [A2A call] span="a2a.reviewer.evaluate"
            ├── [Sandbox] span="sandbox.exec.go_test_race"
            └── [LLM Router] span="llm.chat_completion"
```

### Span attributes

| Span | Attributes |
|---|---|
| `a2a.*` | `agent.id`, `agent.name`, `task.id`, `rpc.method` |
| `llm.*` | `llm.model`, `llm.provider`, `llm.tokens.input`, `llm.tokens.output`, `llm.cost`, `llm.ttft_ms` |
| `sandbox.*` | `sandbox.command`, `sandbox.exit_code`, `sandbox.duration_ms` |
| `pipeline.*` | `task.id`, `task.phase`, `task.iteration` |
| `guardrail.*` | `guardrail.type`, `guardrail.blocked` (bool), `agent.id` |

### Export

OTel Collector конфигурация:

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: "0.0.0.0:4317"

processors:
  batch:
    timeout: 5s
    send_batch_size: 1024

exporters:
  prometheus:
    endpoint: "0.0.0.0:8889"
  logging:
    loglevel: info

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [logging]
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [prometheus]
```

## Metrics (Prometheus)

### LLM Router метрики

| Метрика | Тип | Labels | Описание |
|---|---|---|---|
| `finit_llm_requests_total` | counter | provider, model, status, agent | Общее число LLM запросов |
| `finit_llm_ttft_seconds` | histogram | provider, model | Time to First Token |
| `finit_llm_tpot_seconds` | histogram | provider, model | Time per Output Token |
| `finit_llm_request_duration_seconds` | histogram | provider, model, status | Полная латентность запроса |
| `finit_llm_tokens_total` | counter | provider, model, direction | Суммарные токены (input/output) |
| `finit_llm_cost_dollars` | counter | provider, model | Суммарная стоимость |
| `finit_llm_provider_health` | gauge | provider | 1=healthy, 0=unhealthy |
| `finit_llm_provider_latency_ewma_seconds` | gauge | provider, model | EWMA латентность |
| `finit_llm_circuit_breaker_state` | gauge | provider | 0=closed, 1=open, 0.5=half-open |
| `finit_llm_active_requests` | gauge | provider | Текущие in-flight запросы |

**Histogram buckets для TTFT:** `[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]` seconds

**Histogram buckets для TPOT:** `[0.01, 0.025, 0.05, 0.1, 0.25]` seconds

### Pipeline метрики

| Метрика | Тип | Labels | Описание |
|---|---|---|---|
| `finit_task_total` | counter | status | Задачи по статусам |
| `finit_task_duration_seconds` | histogram | phase, status | Длительность по фазам |
| `finit_task_iterations_total` | histogram | - | Число итераций до завершения |
| `finit_task_budget_utilization` | histogram | - | spent_tokens / max_tokens |
| `finit_agent_invocations_total` | counter | agent, method, status | Вызовы агентов |
| `finit_agent_duration_seconds` | histogram | agent, method | Латентность агентов |
| `finit_agent_health` | gauge | agent | 1=healthy, 0=unhealthy |
| `finit_workspace_build_duration_seconds` | histogram | base_image | Время сборки workspace |
| `finit_workspace_reuse_total` | counter | - | Переиспользования workspace |

### Guardrails метрики

| Метрика | Тип | Labels | Описание |
|---|---|---|---|
| `finit_guardrail_checks_total` | counter | type, result | Проверки (passed/blocked) |
| `finit_guardrail_blocks_total` | counter | type, agent | Заблокированные запросы |
| `finit_guardrail_latency_seconds` | histogram | type | Латентность проверки |

### Infrastructure метрики

Собираются через cAdvisor / node_exporter:

| Метрика | Описание |
|---|---|
| `container_cpu_usage_seconds_total` | CPU per container |
| `container_memory_usage_bytes` | RAM per container |
| `container_network_receive_bytes_total` | Network in |
| `container_network_transmit_bytes_total` | Network out |

## Grafana дашборды

### Dashboard 1: LLM Overview

| Panel | Тип | Query |
|---|---|---|
| Request Rate | Time series | `rate(finit_llm_requests_total[5m])` by provider |
| TTFT Distribution | Heatmap | `finit_llm_ttft_seconds` by provider |
| TPOT Distribution | Heatmap | `finit_llm_tpot_seconds` by provider |
| Cost Accumulation | Time series | `increase(finit_llm_cost_dollars[1h])` by provider |
| Provider Health | Status map | `finit_llm_provider_health` |
| Traffic per Provider | Pie chart | `sum(finit_llm_requests_total)` by provider |
| Token Usage | Time series | `rate(finit_llm_tokens_total[5m])` by direction |
| Error Rate | Time series | `rate(finit_llm_requests_total{status!="200"}[5m])` |

### Dashboard 2: Task Pipeline

| Panel | Тип | Query |
|---|---|---|
| Task Flow | State timeline | Task status transitions |
| Phase Durations | Box plot | `finit_task_duration_seconds` by phase |
| Success Rate | Gauge | `sum(finit_task_total{status="completed"}) / sum(finit_task_total)` |
| Iteration Distribution | Histogram | `finit_task_iterations_total` |
| Budget Utilization | Histogram | `finit_task_budget_utilization` |
| Escalation Rate | Gauge | `sum(finit_task_total{status="escalated"}) / sum(finit_task_total)` |

### Dashboard 3: Agent Health

| Panel | Тип | Query |
|---|---|---|
| Agent Status | Status map | `finit_agent_health` |
| Invocation Rate | Time series | `rate(finit_agent_invocations_total[5m])` by agent |
| Agent Latency | Time series | `finit_agent_duration_seconds` p50/p95 by agent |
| Container CPU | Time series | `container_cpu_usage_seconds_total` per agent |
| Container RAM | Time series | `container_memory_usage_bytes` per agent |
| Guardrail Blocks | Time series | `rate(finit_guardrail_blocks_total[5m])` by type |

## MLFlow

### Experiment Structure

```
Experiment: "finit-tasks"
  └── Run: "task-123"
       ├── Parameters:
       │    ├── input: "Add health check..."
       │    ├── model: "qwen3-72b"
       │    └── max_iterations: 3
       ├── Metrics:
       │    ├── total_tokens: 45000
       │    ├── total_cost: 0.12
       │    ├── iterations: 1
       │    ├── review_pass: 1 (bool)
       │    └── duration_seconds: 180
       └── Nested Runs:
            ├── Run: "bootstrap"
            │    └── Artifacts: [llm-call-1.json]
            ├── Run: "work-iter-1"
            │    └── Artifacts: [llm-call-2.json, llm-call-3.json]
            └── Run: "review-iter-1"
                 └── Artifacts: [llm-call-4.json]
```

### LLM Call Artifact

```json
{
  "request_id": "req-456",
  "timestamp": "2026-04-01T10:05:23Z",
  "agent": "worker",
  "provider": "vllm-local",
  "model": "qwen3-72b",
  "temperature": 0.3,
  "input_tokens": 3200,
  "output_tokens": 1800,
  "cost_dollars": 0.0,
  "ttft_ms": 245,
  "total_latency_ms": 4500,
  "status": 200,
  "prompt": "[logged only if mlflow.log_prompts=true]",
  "response": "[logged only if mlflow.log_prompts=true]"
}
```

## Health Checks

| Сервис | Endpoint | Проверяет | Interval |
|---|---|---|---|
| Orchestrator | `GET /health` | PostgreSQL ping | 10s |
| LLM Router | `GET /health` | >= 1 healthy provider | 10s |
| Bootstrapper | `GET /health` | Docker socket accessible | 10s |
| Worker | `GET /health` | Workspace volume mounted | 10s |
| Reviewer | `GET /health` | Workspace volume mounted | 10s |
| PostgreSQL | `SELECT 1` | - | 5s (Docker) |

## Нагрузочное тестирование

### Инструмент

`k6` или `vegeta` для HTTP load testing.

### Сценарии

| Сценарий | Описание | Target |
|---|---|---|
| **LLM Router throughput** | Concurrent requests to /v1/chat/completions | 50 RPS sustained |
| **Provider failover** | Kill primary provider mid-test | < 1s failover |
| **Rate limit handling** | Exceed provider rate limit | Graceful backoff, no 500s |
| **Burst traffic** | 100 concurrent requests | Queue, no crash |
| **Long-running stream** | 5-minute streaming response | No disconnect |

### Метрики теста

- Throughput (req/s)
- Latency p50, p95, p99
- Error rate
- Provider distribution during failover
- Circuit breaker activation count
- Memory/CPU under load

### Пример k6 скрипта

```javascript
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '30s', target: 10 },   // ramp up
    { duration: '2m',  target: 50 },   // sustain
    { duration: '30s', target: 0 },    // ramp down
  ],
};

export default function () {
  const res = http.post('http://localhost:8081/v1/chat/completions', JSON.stringify({
    model: 'mock-llm',
    messages: [{ role: 'user', content: 'Hello' }],
    max_tokens: 100,
  }), {
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer test-token',
      'X-Task-ID': `load-test-${__VU}`,
      'X-Agent-ID': 'load-test',
    },
  });

  check(res, {
    'status is 200': (r) => r.status === 200,
    'latency < 5s': (r) => r.timings.duration < 5000,
  });

  sleep(0.1);
}
```
