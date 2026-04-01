# C4 Container -- Finit Platform

Внутренняя структура платформы: сервисы, хранилища, протоколы связи.

```mermaid
graph TB
    User["Разработчик"]

    subgraph Frontend["Интерфейс"]
        WebUI["WebUI<br/><i>TypeScript, React</i><br/>AG-UI SSE клиент"]
    end

    subgraph Core["Ядро платформы"]
        Orch["Оркестратор<br/><i>Rust</i><br/>LLM-управляемый супервизор,<br/>реестр агентов,<br/>AG-UI SSE сервер"]

        Router["LLM Router<br/><i>Rust</i><br/>Прокси, балансировка,<br/>учёт токенов, защита,<br/>хранилище секретов"]
    end

    subgraph Agents["A2A-агенты"]
        Planner["Planner<br/><i>Python</i><br/>Постановка задачи,<br/>структурированная спека"]
        Boot["Bootstrapper<br/><i>Python</i><br/>Подготовка окружения,<br/>MCP-серверы"]
        Worker["Worker<br/><i>Python</i><br/>Разработка по спеке<br/>через MCP-инструменты"]
        Reviewer["Reviewer<br/><i>Python</i><br/>Ревью по спеке<br/>через MCP-инструменты"]
    end

    subgraph Storage["Хранилища"]
        PG["PostgreSQL<br/><i>Задачи, окружения,<br/>реестр агентов,<br/>учёт токенов,<br/>память, секреты</i>"]
        Volumes["Docker Volumes<br/><i>Файлы окружений</i>"]
    end

    subgraph Observability["Наблюдаемость"]
        OTel["OTel Collector<br/><i>Трассы, метрики, логи</i>"]
        Prom["Prometheus<br/><i>Хранение метрик</i>"]
        Graf["Grafana<br/><i>Дашборды</i>"]
        MLF["MLFlow<br/><i>Трассировка LLM</i>"]
    end

    subgraph External["Внешние сервисы"]
        LLM1["LLM-провайдер 1<br/><i>vLLM / Ollama</i>"]
        LLM2["LLM-провайдер 2<br/><i>OpenAI / Anthropic</i>"]
        MockLLM["Mock LLM<br/><i>Тестовый провайдер</i>"]
    end

    User -->|"HTTP"| WebUI
    WebUI -->|"AG-UI SSE"| Orch
    WebUI -->|"REST API"| Orch

    Orch -->|"A2A JSON-RPC"| Planner
    Orch -->|"A2A JSON-RPC"| Boot
    Orch -->|"A2A JSON-RPC"| Worker
    Orch -->|"A2A JSON-RPC"| Reviewer

    Planner -->|"OpenAI API"| Router
    Boot -->|"OpenAI API"| Router
    Worker -->|"OpenAI API"| Router
    Reviewer -->|"OpenAI API"| Router

    Router -->|"OpenAI API"| LLM1
    Router -->|"OpenAI API"| LLM2
    Router -->|"OpenAI API"| MockLLM

    Orch -->|"SQL"| PG
    Router -->|"SQL"| PG
    Router -->|"MLFlow API"| MLF

    Boot -->|"Docker API"| Volumes
    Worker -.->|"MCP"| Volumes
    Reviewer -.->|"MCP (ro)"| Volumes

    Orch -->|"OTLP"| OTel
    Router -->|"OTLP"| OTel
    Boot -->|"OTLP"| OTel
    Worker -->|"OTLP"| OTel
    Reviewer -->|"OTLP"| OTel

    OTel -->|"remote write"| Prom
    Prom -->|"query"| Graf

    style Core fill:#e3f2fd,stroke:#1565C0,stroke-width:2px
    style Agents fill:#fff8e1,stroke:#F57F17,stroke-width:2px
    style Storage fill:#e8f5e9,stroke:#2E7D32,stroke-width:2px
    style Observability fill:#fce4ec,stroke:#AD1457,stroke-width:2px
    style Frontend fill:#f3e5f5,stroke:#6A1B9A,stroke-width:2px
    style External fill:#efebe9,stroke:#4E342E,stroke-width:2px
```

## Протоколы связи

| Связь | Протокол | Формат | Потоковая передача |
|---|---|---|---|
| WebUI → Оркестратор | HTTP REST + AG-UI SSE | JSON | Да (SSE) |
| Оркестратор → Агенты | A2A (JSON-RPC 2.0) | JSON | Да (SSE) |
| Агенты → LLM Router | OpenAI-compatible API | JSON | Да (SSE) |
| LLM Router → Провайдеры | OpenAI-compatible API | JSON | Да (SSE) |
| Оркестратор → PostgreSQL | SQL (`sqlx`) | Binary | Нет |
| LLM Router → PostgreSQL | SQL (`sqlx`) | Binary | Нет |
| Все → OTel Collector | OTLP (gRPC) | Protobuf | Нет |
| LLM Router → MLFlow | MLFlow REST API | JSON | Нет |
