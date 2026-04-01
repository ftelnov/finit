# Спецификация: LLM Router

## Назначение

OpenAI-compatible прокси, единая точка доступа к LLM-провайдерам. Обеспечивает маршрутизацию, балансировку, трекинг токенов и стоимости, guardrails, streaming.

## Стек

- **Язык**: Rust
- **HTTP**: `axum`
- **Streaming**: SSE passthrough (chunked transfer)
- **Метрики**: `opentelemetry` crate → Prometheus
- **Трассировка**: MLFlow REST API
- **Хранение**: PostgreSQL (`sqlx`)

## API

### Основной endpoint

```
POST /v1/chat/completions
Content-Type: application/json
Authorization: Bearer <agent-jwt>
X-Task-ID: task-123
X-Agent-ID: worker
```

**Request body**: OpenAI ChatCompletion format.

**Response**: OpenAI ChatCompletion format (JSON или SSE stream при `stream: true`).

### Management API

| Endpoint | Method | Описание | Auth |
|---|---|---|---|
| `POST /v1/providers` | POST | Регистрация провайдера | admin JWT |
| `GET /v1/providers` | GET | Список провайдеров + статус | any JWT |
| `PUT /v1/providers/{id}` | PUT | Обновление провайдера | admin JWT |
| `DELETE /v1/providers/{id}` | DELETE | Удаление провайдера | admin JWT |
| `GET /v1/usage` | GET | Общая статистика | any JWT |
| `GET /v1/usage/tasks/{id}` | GET | Статистика по задаче | any JWT |
| `GET /v1/usage/agents/{id}` | GET | Статистика по агенту | any JWT |
| `GET /health` | GET | Health check | no auth |
| `GET /metrics` | GET | Prometheus metrics | no auth |

### Модель провайдера

```json
{
  "id": "vllm-local",
  "name": "vLLM Local",
  "url": "http://localhost:8000/v1",
  "api_key": "sk-...",
  "models": {
    "qwen3-72b": {
      "pricing": { "input_per_1m_tokens": 0.0, "output_per_1m_tokens": 0.0 },
      "context_window": 32768,
      "max_output_tokens": 8192
    },
    "deepseek-v3": {
      "pricing": { "input_per_1m_tokens": 0.27, "output_per_1m_tokens": 1.10 },
      "context_window": 65536,
      "max_output_tokens": 8192
    }
  },
  "weight": 10,
  "priority": 1,
  "max_concurrent": 5,
  "rate_limit": {
    "requests_per_minute": 60,
    "tokens_per_minute": 100000
  },
  "timeout_ms": 30000,
  "health_check_interval_s": 10,
  "status": "healthy",
  "stats": {
    "ewma_latency_ms": 245,
    "total_requests": 1523,
    "error_count_30s": 0
  }
}
```

## Балансировка

### Алгоритм выбора провайдера

```
1. Model Router: запрос содержит model="qwen3-72b"
   → filter providers where "qwen3-72b" in models
   → pool = [vllm-local, cloud-backup]

2. Health Filter: исключить провайдеров с status != "healthy"
   → pool = [vllm-local, cloud-backup]  (оба здоровы)

3. Circuit Breaker Filter: исключить провайдеров с открытым circuit breaker
   → pool = [vllm-local, cloud-backup]

4. Strategy Selection (по конфигурации):

   round-robin:
     → next = pool[counter % len(pool)]

   weighted:
     → random selection proportional to weights
     → vllm-local (weight=10) vs cloud-backup (weight=1)
     → 91% traffic → vllm-local

   latency-based:
     → sort by ewma_latency_ms ascending
     → select first (lowest latency)

   Комбинированная стратегия (default):
     → health-aware + latency-based
```

### EWMA Latency

Exponentially Weighted Moving Average с alpha=0.3:

```
ewma = alpha * current_latency + (1 - alpha) * previous_ewma
```

Обновляется после каждого успешного запроса. Используется для latency-based routing.

### Circuit Breaker

Per-provider, конфигурируемый:

| Параметр | Default | Описание |
|---|---|---|
| `failure_threshold` | 3 | Количество ошибок для открытия |
| `failure_window_s` | 30 | Окно подсчета ошибок |
| `cooldown_s` | 30 | Время cooldown после открытия |
| `half_open_requests` | 1 | Пробный запрос при half-open |

States: `closed` → `open` → `half-open` → `closed`

Transitions:
- `closed` → `open`: failure count >= `failure_threshold` within `failure_window_s`
- `open` → `half-open`: `cooldown_s` elapsed
- `half-open` → `closed`: probe request succeeds
- `half-open` → `open`: probe request fails (reset cooldown timer)

Concurrent requests during `half-open`: only `half_open_requests` are forwarded as probes, the rest are routed to other providers.

## Retry и Failover

При ошибке провайдера (5xx, timeout, connection refused) Router автоматически пробует следующий:

```
1. Select provider from pool (by strategy)
2. Forward request
3. If error (5xx / timeout / connection error):
   a. Increment provider failure counter (for circuit breaker)
   b. If other providers available in pool → retry with next provider
   c. If no more providers → return 503 to caller
4. If 429 (rate limit):
   a. Exponential backoff: 1s, 2s, 4s (max 3 retries on same provider)
   b. If still 429 after retries → try next provider
   c. If no more providers → return 429 to caller
5. If success → return response (stream or complete)
```

**Ограничения:**
- Max 1 failover attempt per request (total 2 providers tried)
- Backoff только для 429, не для 5xx (immediate failover)
- Streaming запросы, у которых уже начал стримиться ответ, не подлежат failover (stream уже передается клиенту)

## Streaming

SSE passthrough: Router не буферизирует streaming ответы, а проксирует `data:` chunks по мере получения от провайдера.

```
Agent ← SSE ← Router ← SSE ← Provider

Каждый chunk:
  data: {"id":"...","choices":[{"delta":{"content":"..."}}]}
```

**TTFT** измеряется как время между отправкой запроса провайдеру и получением первого `data:` chunk.

**TPOT** измеряется как среднее время между последовательными `data:` chunks.

## Token Counting

Подсчет токенов выполняется после завершения запроса:

- **Input tokens**: из `usage.prompt_tokens` в ответе провайдера
- **Output tokens**: из `usage.completion_tokens` в ответе провайдера
- **Streaming**: Router добавляет `stream_options: {"include_usage": true}` к запросу. Usage приходит в финальном chunk. Если stream прерван до финального chunk -- используется fallback.
- **Fallback** (если провайдер не возвращает usage): подсчет по количеству streamed chunks * estimated tokens per chunk. Точный подсчет через tiktoken не используется для streaming (требует буферизации).

Стоимость рассчитывается по ценам **модели** (из конфигурации провайдера, `models.{model}.pricing`):

```
cost = (input_tokens / 1_000_000) * input_price +
       (output_tokens / 1_000_000) * output_price
```

Результат записывается в PostgreSQL:

```sql
INSERT INTO llm_usage (
  request_id, task_id, agent_id, provider_id, model,
  input_tokens, output_tokens, cost_dollars,
  ttft_ms, total_latency_ms, status, created_at
) VALUES (...);
```

## Guardrails

### Prompt Injection Detection

Middleware сканирует `messages[].content` на паттерны:

```
Patterns:
  - "ignore previous instructions"
  - "you are now"
  - "system: " (role injection в user message)
  - "```system" (hidden system prompt)
  - регулируемый threshold по confidence score
```

При детекции: `403 Forbidden`, audit log entry, OTel span event.

### Secret Leakage Prevention (LLM Firewall)

Aho-Corasick multi-pattern matcher по всем known secret values из centralized secrets store:

1. При старте и при обновлении secrets store: загрузить all secret values, построить Aho-Corasick автомат
2. Для каждого запроса: O(n) scan по полному тексту prompt
3. При совпадении: `403 Forbidden`, audit log (key, не value), OTel span event

Минимальная длина секрета для включения в паттерн: 8 символов (избежание false positives).

## Secrets Store

Secrets store -- часть платформы (PostgreSQL, encrypted at rest). Агенты **не имеют API для чтения значений секретов**. Единственный способ использования -- монтирование в workspace sandbox.

### Admin API (управление секретами)

| Endpoint | Method | Описание | Auth |
|---|---|---|---|
| `PUT /v1/secrets/{key}` | PUT | Сохранить секрет | admin JWT |
| `DELETE /v1/secrets/{key}` | DELETE | Удалить секрет | admin JWT |
| `GET /v1/secrets` | GET | Список ключей (без значений) | admin JWT |

### Workspace Mount API

| Endpoint | Method | Описание | Auth |
|---|---|---|---|
| `POST /v1/workspaces/{id}/secrets` | POST | Примонтировать секрет в workspace | orchestrator JWT |

```json
POST /v1/workspaces/ws-abc/secrets
{
  "key": "GITHUB_TOKEN",
  "mount_as": "env",       // "env" | "file"
  "target": "GITHUB_TOKEN" // env var name or path in /workspace/.secrets/
}
```

При создании sandbox контейнера, примонтированные секреты передаются как env vars или bind-mounted files. Агент использует их неявно (код внутри sandbox читает env/file), но не имеет программного доступа к значению через platform API.

### LLM Firewall

Aho-Corasick scan всех LLM prompts по known secret values из store. Последний рубеж: если секрет утек из sandbox в prompt -- запрос блокируется.

### Budget Enforcement

1. Извлечь `X-Task-ID` из заголовка
2. Запрос в PostgreSQL: `SELECT spent_tokens FROM task_budget WHERE task_id = ?`
3. Если `spent_tokens >= max_tokens`: `429 Budget Exhausted`

## Конфигурация

```yaml
llm_router:
  listen: ":8081"
  jwt_secret: "${JWT_SECRET}"

  providers:
    - name: "vllm-local"
      url: "http://localhost:8000/v1"
      api_key: "${VLLM_API_KEY}"
      weight: 10
      models:
        qwen3-72b:
          pricing: { input_per_1m: 0.0, output_per_1m: 0.0 }
        deepseek-v3:
          pricing: { input_per_1m: 0.27, output_per_1m: 1.10 }

    - name: "openai"
      url: "https://api.openai.com/v1"
      api_key: "${OPENAI_API_KEY}"
      weight: 1
      models:
        gpt-4o:
          pricing: { input_per_1m: 2.50, output_per_1m: 10.00 }
        gpt-4o-mini:
          pricing: { input_per_1m: 0.15, output_per_1m: 0.60 }

    - name: "mock"
      url: "http://mock-llm:8000/v1"
      weight: 1
      models:
        mock-llm:
          pricing: { input_per_1m: 0.0, output_per_1m: 0.0 }

  balancing:
    strategy: "latency-based"  # round-robin | weighted | latency-based
    health_aware: true

  circuit_breaker:
    failure_threshold: 3
    failure_window_s: 30
    cooldown_s: 30

  guardrails:
    prompt_injection: true
    secret_scan: true
    secrets_file: "/run/secrets/known_secrets"  # one per line

  budget:
    default_max_tokens_per_task: 500000
    default_max_calls_per_task: 50

  mlflow:
    tracking_uri: "http://mlflow:5000"
    experiment_name: "finit-llm"
    log_prompts: false  # true only in debug

  telemetry:
    otlp_endpoint: "otel-collector:4317"
```

## Ошибки и коды ответов

| Код | Ситуация | Response body |
|---|---|---|
| 200 | Успешный запрос | OpenAI ChatCompletion |
| 400 | Невалидный запрос | `{"error": {"message": "...", "type": "invalid_request"}}` |
| 401 | Невалидный JWT | `{"error": {"message": "unauthorized", "type": "auth_error"}}` |
| 403 | Guardrail block | `{"error": {"message": "request blocked by guardrail", "type": "guardrail"}}` |
| 429 | Rate limit / budget | `{"error": {"message": "...", "type": "rate_limit"}}` |
| 503 | Все провайдеры недоступны | `{"error": {"message": "no healthy providers", "type": "service_unavailable"}}` |
| 504 | Timeout | `{"error": {"message": "provider timeout", "type": "timeout"}}` |

## Side effects

- Каждый запрос записывает row в `llm_usage` (PostgreSQL)
- Каждый запрос эмитит OTel span + метрики
- При `mlflow.log_prompts: true` -- полный prompt/response в MLFlow
- Guardrail block записывает audit log entry
