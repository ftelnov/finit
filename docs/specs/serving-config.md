# Спецификация: Serving и Configuration

## Назначение

Описание запуска, конфигурации, секретов, версий моделей и деплоя платформы.

## Deployment: Docker Compose

Все компоненты деплоятся через Docker Compose на одной машине.

### Сервисы

```yaml
# docker-compose.yml (целевая структура)
services:
  # === Core ===
  orchestrator:
    build: ./orchestrator
    ports: ["8080:8080"]
    depends_on:
      postgres: { condition: service_healthy }
    environment:
      - DATABASE_URL=postgres://finit:${PG_PASSWORD}@postgres:5432/finit
      - JWT_SECRET=${JWT_SECRET}
      - LLM_ROUTER_URL=http://llm-router:8081
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
      - OTEL_SERVICE_NAME=finit-orchestrator
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:8080/health"]
      interval: 10s
      timeout: 5s
      retries: 5

  llm-router:
    build: ./llm-router
    ports: ["8081:8081"]
    depends_on:
      postgres: { condition: service_healthy }
    environment:
      - DATABASE_URL=postgres://finit:${PG_PASSWORD}@postgres:5432/finit
      - JWT_SECRET=${JWT_SECRET}
      - MLFLOW_TRACKING_URI=http://mlflow:5000
      - CONFIG_PATH=/etc/finit/router.yaml
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
      - OTEL_SERVICE_NAME=finit-llm-router
    volumes:
      - ./config/router.yaml:/etc/finit/router.yaml:ro
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:8081/health"]
      interval: 10s
      timeout: 5s
      retries: 5

  # === Agents ===
  planner:
    build: ./agents/planner
    environment:
      - AGENT_TYPE=planner
      - LLM_ROUTER_URL=http://llm-router:8081
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
      - OTEL_SERVICE_NAME=finit-planner
    depends_on:
      llm-router: { condition: service_healthy }
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:9000/health')"]
      interval: 10s

  bootstrapper:
    build: ./agents/bootstrapper
    environment:
      - AGENT_TYPE=bootstrapper
      - LLM_ROUTER_URL=http://llm-router:8081
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
      - OTEL_SERVICE_NAME=finit-bootstrapper
    depends_on:
      llm-router: { condition: service_healthy }
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - workspace-volumes:/var/lib/finit/workspaces
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:9001/health')"]
      interval: 10s

  worker:
    build: ./agents/worker
    environment:
      - AGENT_TYPE=worker
      - LLM_ROUTER_URL=http://llm-router:8081
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
      - OTEL_SERVICE_NAME=finit-worker
    depends_on:
      llm-router: { condition: service_healthy }
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:9002/health')"]
      interval: 10s

  reviewer:
    build: ./agents/reviewer
    environment:
      - AGENT_TYPE=reviewer
      - LLM_ROUTER_URL=http://llm-router:8081
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
      - OTEL_SERVICE_NAME=finit-reviewer
    depends_on:
      llm-router: { condition: service_healthy }
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:9003/health')"]
      interval: 10s

  # === Frontend ===
  webui:
    build: ./webui
    ports: ["3000:3000"]
    depends_on:
      orchestrator: { condition: service_healthy }
    environment:
      - ORCHESTRATOR_URL=http://orchestrator:8080

  # === Data Stores ===
  postgres:
    image: postgres:16-alpine
    ports: ["5432:5432"]
    environment:
      - POSTGRES_DB=finit
      - POSTGRES_USER=finit
      - POSTGRES_PASSWORD=${PG_PASSWORD}
    volumes:
      - pg-data:/var/lib/postgresql/data
      - ./migrations:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U finit"]
      interval: 5s
      timeout: 3s
      retries: 5

  # === Observability ===
  otel-collector:
    image: otel/opentelemetry-collector-contrib:latest
    ports:
      - "4317:4317"   # OTLP gRPC
      - "8889:8889"   # Prometheus exporter
    volumes:
      - ./config/otel-collector.yaml:/etc/otelcol/config.yaml:ro

  prometheus:
    image: prom/prometheus:latest
    ports: ["9090:9090"]
    volumes:
      - ./config/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus-data:/prometheus

  grafana:
    image: grafana/grafana:latest
    ports: ["3001:3000"]
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD:-admin}
    volumes:
      - grafana-data:/var/lib/grafana
      - ./config/grafana/dashboards:/etc/grafana/provisioning/dashboards:ro
      - ./config/grafana/datasources:/etc/grafana/provisioning/datasources:ro

  mlflow:
    image: ghcr.io/mlflow/mlflow:latest
    ports: ["5000:5000"]
    command: mlflow server --host 0.0.0.0 --port 5000 --backend-store-uri sqlite:////mlflow/mlflow.db --default-artifact-root /mlflow/artifacts
    volumes:
      - mlflow-data:/mlflow

  # === LLM Providers ===
  mock-llm:
    build: ./mock-llm
    ports: ["8000:8000"]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 5s

volumes:
  pg-data:
  prometheus-data:
  grafana-data:
  mlflow-data:
  workspace-volumes:
```

## Конфигурация

### Environment Variables (.env)

```bash
# === Secrets ===
JWT_SECRET=<random-256-bit-key>
PG_PASSWORD=<postgres-password>
GRAFANA_PASSWORD=admin

# === LLM Providers (optional, configured via Router API) ===
VLLM_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=

# === Feature Flags ===
MLFLOW_LOG_PROMPTS=false    # true: log full prompts/responses
GUARDRAIL_INJECTION=true    # enable prompt injection detection
GUARDRAIL_SECRETS=true      # enable secret leakage scan

# === Limits ===
DEFAULT_MAX_TOKENS=500000
DEFAULT_MAX_ITERATIONS=3
DEFAULT_MAX_TASK_DURATION=30m
```

### Router Config (config/router.yaml)

```yaml
listen: ":8081"

providers:
  - name: mock
    url: "http://mock-llm:8000/v1"
    weight: 1
    models:
      mock-llm:
        pricing: { input_per_1m: 0.0, output_per_1m: 0.0 }

balancing:
  strategy: "latency-based"
  health_aware: true

circuit_breaker:
  failure_threshold: 3
  failure_window_s: 30
  cooldown_s: 30

guardrails:
  prompt_injection: true
  secret_scan: true

budget:
  default_max_tokens: 500000
  default_max_calls: 50

mlflow:
  tracking_uri: "http://mlflow:5000"
  experiment_name: "finit-llm"
  log_prompts: false

telemetry:
  otlp_endpoint: "otel-collector:4317"
```

### Prometheus Config (config/prometheus.yml)

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'otel-collector'
    static_configs:
      - targets: ['otel-collector:8889']

  - job_name: 'orchestrator'
    static_configs:
      - targets: ['orchestrator:8080']
    metrics_path: '/metrics'

  - job_name: 'llm-router'
    static_configs:
      - targets: ['llm-router:8081']
    metrics_path: '/metrics'
```

## Секреты

### Management

| Секрет | Где хранится | Кто использует |
|---|---|---|
| `JWT_SECRET` | `.env` | Orchestrator (sign), LLM Router (verify), Agents (verify) |
| `PG_PASSWORD` | `.env` | Orchestrator, LLM Router |
| `VLLM_API_KEY` | Router config / API | LLM Router → Provider |
| `OPENAI_API_KEY` | Router config / API | LLM Router → Provider |
| `GRAFANA_PASSWORD` | `.env` | Grafana |

### Production рекомендации

- `.env` файл не коммитится (в .gitignore)
- Docker Secrets для production:
  ```yaml
  secrets:
    jwt_secret:
      file: ./secrets/jwt_secret.txt
  ```
- LLM API keys регистрируются через Router Management API, не через env vars

## Версии моделей

### Конфигурация через Router

Модели привязаны к провайдерам. Агенты указывают `model` в запросе, Router маршрутизирует к нужному провайдеру:

```
Agent request: model="qwen3-72b"
  → Router finds: provider "vllm-local" serves "qwen3-72b"
  → Forward to http://vllm-local:8000/v1/chat/completions

Agent request: model="gpt-4o"
  → Router finds: provider "openai" serves "gpt-4o"
  → Forward to https://api.openai.com/v1/chat/completions
```

### Смена модели

Смена модели для всех агентов -- изменение env var `LLM_MODEL` или конфигурация через Router API. Агенты не хардкодят модель.

## Makefile

```makefile
.PHONY: up down test logs build clean

# Start all services
up:
	docker compose up --build -d

# Stop all services
down:
	docker compose down

# Run E2E tests
test:
	docker compose --profile test up --build --abort-on-container-exit e2e-tests

# View logs
logs:
	docker compose logs -f

# View specific service logs
logs-%:
	docker compose logs -f $*

# Build only
build:
	docker compose build

# Clean everything (volumes, images)
clean:
	docker compose down -v --rmi local

# Open Grafana
grafana:
	open http://localhost:3001

# Open MLFlow
mlflow:
	open http://localhost:5000

# Open WebUI
ui:
	open http://localhost:3000

# Register LLM provider
register-provider:
	@echo "Usage: make register-provider URL=http://... MODELS=model1,model2"
	curl -X POST http://localhost:8081/v1/providers \
		-H "Content-Type: application/json" \
		-d '{"name":"custom","url":"$(URL)","models":["$(MODELS)"]}'

# Load test (requires k6)
loadtest:
	k6 run tests/load/llm-router.js
```

## Порты

| Сервис | Порт | Описание |
|---|---|---|
| Orchestrator | 8080 | REST API + AG-UI SSE |
| LLM Router | 8081 | OpenAI-compatible API + Management |
| WebUI | 3000 | Фронтенд |
| Planner | 9000 | A2A server (internal) |
| Bootstrapper | 9001 | A2A server (internal) |
| Worker | 9002 | A2A server (internal) |
| Reviewer | 9003 | A2A server (internal) |
| PostgreSQL | 5432 | Data store |
| Mock LLM | 8000 | Test provider |
| OTel Collector | 4317 | OTLP gRPC |
| Prometheus | 9090 | Metrics |
| Grafana | 3001 | Dashboards |
| MLFlow | 5000 | LLM tracing |

## Системные требования

| Ресурс | Minimum | Recommended |
|---|---|---|
| RAM | 16 GB | 32 GB |
| CPU | 4 cores | 8+ cores |
| Disk | 50 GB SSD | 100 GB SSD |
| GPU | - | NVIDIA (для vLLM) |
| Docker | 24.0+ | Latest |
| Docker Compose | v2.20+ | Latest |
| OS | macOS 13+ / Linux 5.15+ | - |
