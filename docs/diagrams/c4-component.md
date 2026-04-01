# C4 Component -- Оркестратор и LLM Router

Внутреннее устройство двух ключевых сервисов.

## Оркестратор

```mermaid
graph TB
    subgraph Orchestrator["Оркестратор (Rust)"]
        direction TB

        subgraph API["HTTP API"]
            REST["REST API<br/><i>/api/tasks, /api/agents</i>"]
            AGUI["AG-UI SSE<br/><i>/ag-ui/tasks/{id}/events</i>"]
            Health["Проверка работоспособности<br/><i>/health, /metrics</i>"]
        end

        subgraph Core["Ядро"]
            TM["Управление задачами<br/><i>Машина состояний,<br/>бюджет</i>"]
            Supervisor["Супервизор (LLM)<br/><i>Динамическая маршрутизация,<br/>разрешение input_required</i>"]
            Registry["Реестр агентов<br/><i>Карточки, CRUD,<br/>проверка работоспособности</i>"]
            Memory["Память<br/><i>Правила, факты,<br/>семантический поиск</i>"]
            Auth["Авторизация<br/><i>JWT, права доступа</i>"]
        end

        subgraph Transport["Транспорт"]
            A2AClient["A2A-клиент<br/><i>JSON-RPC 2.0,<br/>потоковый приём</i>"]
            EventBus["Шина событий<br/><i>AG-UI,<br/>запись в PostgreSQL</i>"]
        end

        subgraph Persistence["Хранение"]
            PGRepo["PostgreSQL<br/><i>Задачи, окружения,<br/>агенты, память,<br/>события</i>"]
        end

        REST --> TM
        REST --> Registry
        AGUI --> EventBus
        Health --> PGRepo
        TM --> Supervisor
        Supervisor --> A2AClient
        Supervisor --> Memory
        A2AClient --> EventBus
        TM --> PGRepo
        EventBus --> PGRepo
        Registry --> PGRepo
        Memory --> PGRepo
        Supervisor --> Auth
        A2AClient --> Auth
    end

    style API fill:#e3f2fd,stroke:#1565C0
    style Core fill:#fff8e1,stroke:#F57F17
    style Transport fill:#e8f5e9,stroke:#2E7D32
    style Persistence fill:#fce4ec,stroke:#AD1457
```

### Компоненты оркестратора

| Компонент | Ответственность |
|---|---|
| **REST API** | CRUD задач, управление агентами |
| **AG-UI SSE** | Потоковая доставка событий в WebUI |
| **Управление задачами** | Машина состояний задачи, бюджет, переходы |
| **Супервизор (LLM)** | Динамический выбор следующего агента, разрешение `input_required`, эскалация |
| **Реестр агентов** | Хранение карточек агентов, периодическая проверка работоспособности |
| **Память** | Правила (принудительные) и факты (индексируемые) по задачам и окружениям |
| **Авторизация** | Генерация JWT, проверка прав доступа |
| **A2A-клиент** | Вызов агентов по JSON-RPC 2.0, приём потоковых ответов |
| **Шина событий** | Трансляция A2A-событий в формат AG-UI, запись в PostgreSQL |
| **PostgreSQL** | Единственное хранилище: задачи, окружения, реестр, память, события |

---

## LLM Router

```mermaid
graph TB
    subgraph Router["LLM Router (Rust)"]
        direction TB

        subgraph Ingress["Вход"]
            API["OpenAI-compatible API<br/><i>POST /v1/chat/completions</i>"]
            MgmtAPI["API управления<br/><i>/v1/providers, /v1/usage,<br/>/v1/secrets</i>"]
        end

        subgraph Middleware["Цепочка проверок"]
            AuthMW["Авторизация<br/><i>Проверка JWT</i>"]
            GuardMW["Защита<br/><i>Обнаружение инъекций,<br/>поиск секретов,<br/>проверка бюджета</i>"]
        end

        subgraph Routing["Маршрутизация и балансировка"]
            ModelRouter["Маршрутизатор моделей<br/><i>модель → пул провайдеров</i>"]
            LB["Балансировщик<br/><i>round-robin, взвешенный,<br/>по задержке,<br/>с учётом состояния</i>"]
            CB["Circuit Breaker<br/><i>Счётчик ошибок →<br/>пауза → восстановление</i>"]
        end

        subgraph Tracking["Учёт и телеметрия"]
            TokenCounter["Счётчик токенов<br/><i>вход/выход,<br/>расчёт стоимости</i>"]
            Metrics["Отправка метрик<br/><i>TTFT, TPOT, задержка<br/>→ OTel/Prometheus</i>"]
            MLFlowLog["Журнал MLFlow<br/><i>Промпт, ответ,<br/>метаданные</i>"]
        end

        subgraph ProviderMgmt["Управление провайдерами"]
            ProviderReg["Реестр провайдеров<br/><i>Динамический CRUD,<br/>конфигурация, цены</i>"]
            HealthChecker["Проверка состояния<br/><i>Периодический /health</i>"]
        end

        subgraph Secrets["Хранилище секретов"]
            SecretStore["Секреты<br/><i>CRUD, монтирование<br/>в окружения</i>"]
        end

        API --> AuthMW --> GuardMW --> ModelRouter
        ModelRouter --> LB --> CB
        CB --> ProviderProxy["Прокси провайдера<br/><i>Сквозная передача SSE</i>"]
        ProviderProxy --> TokenCounter --> Metrics
        TokenCounter --> MLFlowLog
        MgmtAPI --> ProviderReg
        MgmtAPI --> SecretStore
        ProviderReg --> ModelRouter
        HealthChecker --> CB
        GuardMW --> SecretStore
    end

    style Ingress fill:#e3f2fd,stroke:#1565C0
    style Middleware fill:#ffebee,stroke:#C62828
    style Routing fill:#fff8e1,stroke:#F57F17
    style Tracking fill:#e8f5e9,stroke:#2E7D32
    style ProviderMgmt fill:#f3e5f5,stroke:#6A1B9A
    style Secrets fill:#fce4ec,stroke:#AD1457
```

### Компоненты LLM Router

| Компонент | Ответственность |
|---|---|
| **OpenAI API** | Приём запросов в OpenAI-compatible формате, потоковая передача SSE |
| **API управления** | CRUD провайдеров, статистика, управление секретами |
| **Авторизация** | Проверка JWT, извлечение идентификатора и прав агента |
| **Защита** | Обнаружение инъекций в промпты, поиск утечек секретов (Aho-Corasick), проверка бюджета |
| **Маршрутизатор моделей** | Соответствие имени модели → пул провайдеров |
| **Балансировщик** | Выбор конкретного провайдера из пула по стратегии |
| **Circuit Breaker** | Счётчик ошибок → пауза → пробный запрос → восстановление |
| **Прокси провайдера** | Проксирование HTTP с передачей SSE без буферизации |
| **Счётчик токенов** | Подсчёт входных/выходных токенов, расчёт стоимости по модели |
| **Отправка метрик** | TTFT, TPOT, общая задержка → OTel → Prometheus |
| **Журнал MLFlow** | Запись промпта/ответа/метаданных в MLFlow |
| **Реестр провайдеров** | Хранение конфигурации провайдеров, динамическое обновление |
| **Проверка состояния** | Периодическая проверка провайдеров, обновление статуса |
| **Хранилище секретов** | CRUD секретов, монтирование в окружения (env/file), паттерны для LLM Firewall |
