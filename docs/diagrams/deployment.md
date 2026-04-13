# Диаграмма деплоя

Физическое размещение компонентов на инфраструктуре.

## Single-Machine Deployment (MVP)

```mermaid
graph TB
    subgraph Host["Host Machine (32GB+ RAM, 8+ cores, SSD)"]
        direction TB

        subgraph DockerEngine["Docker Engine"]
            direction TB

            subgraph Network_Bridge["Bridge Network: finit-net"]
                direction TB

                subgraph Frontend["Frontend"]
                    WebUI["WebUI<br/><i>:3000<br/>React, TypeScript<br/>128MB RAM</i>"]
                end

                subgraph CoreServices["Core Services"]
                    Orch["Orchestrator<br/><i>:8080<br/>Rust, axum<br/>512MB RAM</i>"]
                    Router["LLM Router<br/><i>:8081<br/>Rust, axum<br/>512MB RAM</i>"]
                end

                subgraph Agents["A2A Agents"]
                    Planner["Planner<br/><i>:9000<br/>Python<br/>512MB RAM</i>"]
                    Bootstrapper["Bootstrapper<br/><i>:9001<br/>Python<br/>512MB RAM</i>"]
                    Worker["Worker<br/><i>:9002<br/>Python<br/>1GB RAM</i>"]
                    Reviewer["Reviewer<br/><i>:9003<br/>Python<br/>512MB RAM</i>"]
                end

                subgraph DataStores["Data Stores"]
                    PG["PostgreSQL 16<br/><i>:5432<br/>+ pgvector<br/>2GB RAM</i>"]
                end

                subgraph Observability["Observability Stack"]
                    OTel["OTel Collector<br/><i>:4317 (gRPC)<br/>:8889 (Prometheus)<br/>256MB RAM</i>"]
                    Prom["Prometheus<br/><i>:9090<br/>1GB RAM</i>"]
                    Grafana["Grafana<br/><i>:3001<br/>256MB RAM</i>"]
                    MLFlow["MLFlow<br/><i>:5000<br/>512MB RAM</i>"]
                end

                subgraph LLMProviders["LLM Providers"]
                    MockLLM["Mock LLM<br/><i>:8000<br/>256MB RAM</i>"]
                end
            end

            subgraph Sandboxes["Ephemeral Sandboxes (per-task)"]
                WS1["workspace-task-1<br/><i>CPU: 2, RAM: 4GB<br/>PIDs: 256<br/>network: isolated</i>"]
                WS2["workspace-review-task-1<br/><i>CPU: 1, RAM: 2GB<br/>PIDs: 128<br/>network: none</i>"]
            end
        end

        subgraph Volumes["Docker Volumes"]
            PGData["pg-data<br/><i>PostgreSQL data</i>"]
            PromData["prometheus-data<br/><i>Metrics TSDB</i>"]
            GrafanaData["grafana-data<br/><i>Dashboards, config</i>"]
            MLFlowData["mlflow-data<br/><i>Experiments, artifacts</i>"]
            WSVolumes["workspace-volumes<br/><i>Project files per workspace</i>"]
        end

        subgraph HostResources["Host Resources"]
            DockerSock["Docker Socket<br/><i>/var/run/docker.sock<br/>(ro → Bootstrapper)</i>"]
            GPU["GPU (optional)<br/><i>NVIDIA<br/>→ vLLM / Ollama</i>"]
        end
    end

    subgraph External["External (optional)"]
        vLLM["vLLM Server<br/><i>Self-hosted<br/>GPU machine</i>"]
        OpenAI["OpenAI API<br/><i>api.openai.com</i>"]
        Anthropic["Anthropic API<br/><i>api.anthropic.com</i>"]
    end

    WebUI -->|"HTTP REST +<br/>AG-UI SSE"| Orch
    Orch -->|"A2A JSON-RPC"| Planner
    Orch -->|"A2A JSON-RPC"| Bootstrapper
    Orch -->|"A2A JSON-RPC"| Worker
    Orch -->|"A2A JSON-RPC"| Reviewer
    Orch -->|"SQL"| PG
    Router -->|"SQL"| PG
    Planner -->|"OpenAI API"| Router
    Bootstrapper -->|"OpenAI API"| Router
    Worker -->|"OpenAI API"| Router
    Reviewer -->|"OpenAI API"| Router
    Router -->|"OpenAI API"| MockLLM
    Router -->|"OpenAI API"| vLLM
    Router -->|"OpenAI API"| OpenAI
    Router -->|"OpenAI API"| Anthropic
    Router -->|"MLFlow REST"| MLFlow
    Orch -->|"OTLP gRPC"| OTel
    Router -->|"OTLP gRPC"| OTel
    Planner -->|"OTLP gRPC"| OTel
    Worker -->|"OTLP gRPC"| OTel
    OTel -->|"Remote write"| Prom
    Prom -->|"Datasource"| Grafana
    Bootstrapper -->|"Docker API"| DockerSock
    PG --- PGData
    Prom --- PromData
    Grafana --- GrafanaData
    MLFlow --- MLFlowData
    Bootstrapper --- WSVolumes

    style Frontend fill:#e3f2fd,stroke:#1565C0
    style CoreServices fill:#fff8e1,stroke:#F57F17
    style Agents fill:#e8f5e9,stroke:#2E7D32
    style DataStores fill:#fce4ec,stroke:#AD1457
    style Observability fill:#f3e5f5,stroke:#6A1B9A
    style LLMProviders fill:#e0f2f1,stroke:#004D40
    style Sandboxes fill:#fff3e0,stroke:#E65100
    style Volumes fill:#eceff1,stroke:#455A64
    style External fill:#fafafa,stroke:#9E9E9E
```

## Требования к ресурсам

### Распределение RAM (минимальная конфигурация, 16GB)

| Группа | Сервисы | RAM |
|---|---|---|
| Core | Orchestrator + LLM Router | ~1 GB |
| Agents | Planner + Bootstrapper + Worker + Reviewer | ~2.5 GB |
| Data | PostgreSQL | ~2 GB |
| Observability | OTel + Prometheus + Grafana + MLFlow | ~2 GB |
| Sandboxes | 1 worker sandbox + 1 review sandbox | ~6 GB |
| OS + Docker | Overhead | ~2.5 GB |
| **Total** | | **~16 GB** |

### Распределение портов

| Диапазон | Назначение | Доступ |
|---|---|---|
| 3000 | WebUI | Пользователь (браузер) |
| 3001 | Grafana | Оператор |
| 5000 | MLFlow | Оператор |
| 8080-8081 | Core Services (Orch, Router) | Internal + API clients |
| 9000-9003 | A2A Agents | Internal only |
| 4317, 8889, 9090 | Observability (OTel, Prometheus) | Internal only |
| 5432 | PostgreSQL | Internal only |

### Сетевая изоляция

```
finit-net (bridge):
  ├── Все platform-сервисы (Orch, Router, Agents, PG, Observability, WebUI)
  └── Могут общаться между собой

workspace-isolated-{task-id} (bridge, no external):
  └── Worker sandbox — доступ только к MCP-серверам внутри контейнера

workspace-review-{task-id} (none):
  └── Reviewer sandbox — без сетевого доступа
```

## Production Deployment (Firecracker)

В production-конфигурации ephemeral sandboxes заменяются на Firecracker microVM:

```
Host Machine
  ├── Docker Engine (platform services)  — без изменений
  └── Firecracker VMM
       ├── microVM: workspace-task-1     — выделенное ядро, KVM изоляция
       └── microVM: workspace-review-1   — read-only rootfs, без сети
```

Преимущества Firecracker:
- Boot time < 150ms (vs Docker ~500ms)
- Полная VM-изоляция через KVM (vs namespace isolation)
- Выделенное ядро — нет shared kernel attack surface
- Минимальный overhead (~5MB RAM per VM)
