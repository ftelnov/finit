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

---

## Worker Agent

Внутреннее устройство Worker — наиболее сложного агента, выполняющего генерацию кода и тестов.

```mermaid
graph TB
    subgraph Worker["Worker Agent (Python)"]
        direction TB

        subgraph A2AServer["A2A Server"]
            RPC["JSON-RPC Handler<br/><i>tasks/send,<br/>tasks/sendSubscribe,<br/>tasks/get, tasks/cancel</i>"]
            Streaming["SSE Streaming<br/><i>TaskStatusUpdate,<br/>TaskArtifactUpdate</i>"]
            AgentCard["Agent Card<br/><i>/.well-known/agent.json</i>"]
            HealthEP["Health Check<br/><i>GET /health</i>"]
        end

        subgraph Core["Ядро"]
            TaskLoop["Task Loop<br/><i>LLM reasoning +<br/>tool call cycle</i>"]
            PromptBuilder["Prompt Builder<br/><i>System prompt +<br/>spec + context +<br/>rules + facts</i>"]
            OutputParser["Output Parser<br/><i>Structured output<br/>JSON validation</i>"]
            InputRequired["Input Required<br/><i>Обнаружение нехватки<br/>инструментов / контекста</i>"]
        end

        subgraph MCPClient["MCP Tools (Workspace)"]
            FileRW["file_read / file_write<br/><i>Чтение и запись<br/>файлов в workspace</i>"]
            BashExec["bash_exec<br/><i>Выполнение команд<br/>в sandbox</i>"]
            TestRun["test_run<br/><i>Запуск тестов,<br/>сбор результатов</i>"]
            LintRun["lint<br/><i>Линтинг кода</i>"]
        end

        subgraph LLMClient["LLM Client"]
            RouterClient["LLM Router Client<br/><i>POST /v1/chat/completions<br/>+ X-Task-ID, X-Agent-ID</i>"]
            StreamHandler["Stream Handler<br/><i>SSE parsing,<br/>token accumulation</i>"]
        end

        subgraph Telemetry["Телеметрия"]
            OTelSpans["OTel Spans<br/><i>task, llm_call,<br/>tool_exec</i>"]
        end

        RPC --> TaskLoop
        Streaming --> TaskLoop
        TaskLoop --> PromptBuilder
        TaskLoop --> RouterClient
        RouterClient --> StreamHandler
        StreamHandler --> OutputParser
        OutputParser --> TaskLoop
        TaskLoop --> FileRW
        TaskLoop --> BashExec
        TaskLoop --> TestRun
        TaskLoop --> LintRun
        TaskLoop --> InputRequired
        TaskLoop --> OTelSpans
        InputRequired --> Streaming
    end

    style A2AServer fill:#e3f2fd,stroke:#1565C0
    style Core fill:#fff8e1,stroke:#F57F17
    style MCPClient fill:#e8f5e9,stroke:#2E7D32
    style LLMClient fill:#f3e5f5,stroke:#6A1B9A
    style Telemetry fill:#fce4ec,stroke:#AD1457
```

### Компоненты Worker Agent

| Компонент | Ответственность |
|---|---|
| **JSON-RPC Handler** | Приём задач от оркестратора по A2A, управление жизненным циклом |
| **SSE Streaming** | Потоковая отправка промежуточных результатов (статус, артефакты) оркестратору |
| **Agent Card** | Описание возможностей агента для автоматической регистрации |
| **Task Loop** | Цикл reasoning: LLM решает → вызов инструмента → анализ результата → следующий шаг |
| **Prompt Builder** | Сборка промпта: системный промпт + спека + возможности workspace + правила + факты |
| **Output Parser** | Валидация JSON structured output от LLM, retry при ошибке парсинга |
| **Input Required** | Обнаружение ситуаций, когда агент не может продолжить (нет инструмента, неясные требования) |
| **MCP Tools** | Взаимодействие с sandbox через MCP-серверы: файлы, команды, тесты, линтинг |
| **LLM Router Client** | HTTP-клиент к LLM Router с заголовками X-Task-ID, X-Agent-ID для трекинга |
| **Stream Handler** | Парсинг SSE-потока от Router, аккумуляция токенов, извлечение structured output |
| **OTel Spans** | Создание span-ов для трассировки: task, llm_call, tool_exec |

### Task Loop (подробно)

```
1. Получить TaskContext от оркестратора (spec, workspace, feedback)
2. Собрать промпт (PromptBuilder)
3. loop:
   a. Вызов LLM через Router → structured output
   b. Парсинг действия: "write_file" | "run_command" | "run_tests" | "request_input" | "submit_result"
   c. Выполнение через MCP tool
   d. Добавить результат в контекст
   e. Если submit_result → собрать артефакты (diff, test results), вернуть оркестратору
   f. Если request_input → вернуть input_required с описанием проблемы
   g. Если budget close → завершить с partial result
4. Стриминг: каждое действие → TaskStatusUpdateEvent через SSE
```

---

## Planner Agent

```mermaid
graph TB
    subgraph Planner["Planner Agent (Python)"]
        direction TB

        subgraph A2A_P["A2A Server"]
            RPC_P["JSON-RPC Handler"]
            Health_P["Health Check"]
        end

        subgraph Core_P["Ядро"]
            SpecGen["Spec Generator<br/><i>LLM → structured spec<br/>(title, criteria,<br/>test plan, domains)</i>"]
            SpecRefine["Spec Refiner<br/><i>Доуточнение по<br/>запросу worker/orch</i>"]
            SchemaVal["Schema Validator<br/><i>JSON Schema для<br/>спецификации</i>"]
        end

        subgraph LLM_P["LLM Client"]
            Router_P["LLM Router Client"]
        end

        RPC_P --> SpecGen
        RPC_P --> SpecRefine
        SpecGen --> Router_P
        SpecRefine --> Router_P
        Router_P --> SchemaVal
    end

    style A2A_P fill:#e3f2fd,stroke:#1565C0
    style Core_P fill:#fff8e1,stroke:#F57F17
    style LLM_P fill:#f3e5f5,stroke:#6A1B9A
```

| Компонент | Ответственность |
|---|---|
| **Spec Generator** | Генерация structured spec по описанию задачи: acceptance criteria, test plan, affected files, domains |
| **Spec Refiner** | Доуточнение спеки по запросу от worker (через оркестратор), сохранение версионности |
| **Schema Validator** | Валидация JSON-структуры спеки против JSON Schema |

---

## Reviewer Agent

```mermaid
graph TB
    subgraph Reviewer["Reviewer Agent (Python)"]
        direction TB

        subgraph A2A_R["A2A Server"]
            RPC_R["JSON-RPC Handler"]
            Health_R["Health Check"]
        end

        subgraph Core_R["Ядро"]
            ReviewLoop["Review Loop<br/><i>LLM reasoning по<br/>спеке + артефактам</i>"]
            EvidenceCollector["Evidence Collector<br/><i>Сбор доказательств<br/>(тесты, lint, анализ)</i>"]
            VerdictGen["Verdict Generator<br/><i>PASS / FAIL +<br/>findings + evidence</i>"]
        end

        subgraph MCPTools_R["MCP Tools (Read-Only)"]
            FileRead_R["file_read<br/><i>Чтение файлов</i>"]
            BashRO_R["bash_exec (ro)<br/><i>Команды без<br/>модификации</i>"]
            TestRun_R["test_run<br/><i>Запуск тестов</i>"]
            Lint_R["lint<br/><i>Линтинг</i>"]
        end

        subgraph LLM_R["LLM Client"]
            Router_R["LLM Router Client"]
        end

        RPC_R --> ReviewLoop
        ReviewLoop --> Router_R
        ReviewLoop --> EvidenceCollector
        EvidenceCollector --> FileRead_R
        EvidenceCollector --> BashRO_R
        EvidenceCollector --> TestRun_R
        EvidenceCollector --> Lint_R
        EvidenceCollector --> VerdictGen
    end

    style A2A_R fill:#e3f2fd,stroke:#1565C0
    style Core_R fill:#fff8e1,stroke:#F57F17
    style MCPTools_R fill:#e8f5e9,stroke:#2E7D32
    style LLM_R fill:#f3e5f5,stroke:#6A1B9A
```

| Компонент | Ответственность |
|---|---|
| **Review Loop** | Цикл ревью: LLM анализирует спеку + артефакты, решает какие проверки запустить |
| **Evidence Collector** | Сбор доказательств: запуск тестов, lint, чтение файлов, анализ diff |
| **Verdict Generator** | Формирование вердикта (PASS/FAIL) с findings и evidence на основе acceptance criteria из спеки |
