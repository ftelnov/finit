# Поток данных -- Finit Platform

Как данные проходят через систему: что хранится, что логируется, что передаётся.

## Сквозной поток данных

```mermaid
flowchart TB
    subgraph Input["Вход"]
        UserInput["Описание задачи<br/>(текст)"]
    end

    subgraph Orchestrator["Оркестратор (LLM-супервизор)"]
        TaskRecord["Запись задачи<br/><i>id, вход, статус,<br/>бюджет, метки времени</i>"]
        AGUI_Events["События AG-UI<br/><i>SSE поток в WebUI</i>"]
        MemoryLookup["Память<br/><i>правила → промпт<br/>факты → семантический поиск</i>"]
    end

    subgraph Planning["Planner"]
        SpecGen["Формирование спеки<br/><i>LLM: критерии приёмки,<br/>план тестирования</i>"]
    end

    subgraph Bootstrap["Bootstrapper"]
        EnvAnalysis["Анализ требований<br/><i>LLM: что нужно</i>"]
        WSBuild["Сборка окружения<br/><i>Dockerfile, зависимости,<br/>MCP-серверы</i>"]
        WSMeta["Метаданные окружения<br/><i>id, возможности,<br/>инструменты</i>"]
    end

    subgraph Work["Worker"]
        CodeGen["Генерация кода<br/><i>LLM через MCP-инструменты</i>"]
        Artifacts["Артефакты<br/><i>diff, тесты,<br/>журналы сборки</i>"]
    end

    subgraph Review["Reviewer"]
        TestRun["Запуск тестов<br/><i>через MCP-инструменты</i>"]
        SemanticReview["Семантическое ревью<br/><i>LLM: соответствие спеке</i>"]
        Report["Отчёт ревью<br/><i>вердикт, находки,<br/>доказательства</i>"]
    end

    subgraph LLMRouter["LLM Router"]
        GuardrailCheck["Проверка защиты"]
        ProviderCall["Вызов провайдера<br/>(потоковая передача)"]
        TokenCount["Подсчёт токенов<br/>+ стоимость"]
    end

    subgraph Storage["Хранение"]
        PG[("PostgreSQL<br/><i>Единственное<br/>хранилище</i>")]
        DockerVol[("Docker Volumes<br/><i>Файлы окружений</i>")]
    end

    subgraph Telemetry["Телеметрия"]
        OTel["OTel Collector"]
        Prom["Prometheus<br/><i>Метрики</i>"]
        MLF["MLFlow<br/><i>Трассы LLM</i>"]
        Graf["Grafana<br/><i>Дашборды</i>"]
    end

    subgraph Output["Выход"]
        Result["Результат<br/><i>diff + отчёт ревью<br/>+ метрики</i>"]
    end

    UserInput --> TaskRecord
    TaskRecord -->|"запись"| PG
    TaskRecord --> AGUI_Events
    TaskRecord --> MemoryLookup
    MemoryLookup -->|"правила + факты"| PG

    TaskRecord --> SpecGen
    SpecGen -->|"LLM"| LLMRouter
    SpecGen -->|"спека"| PG

    TaskRecord --> EnvAnalysis
    EnvAnalysis -->|"LLM"| LLMRouter
    EnvAnalysis --> WSBuild
    WSBuild -->|"сборка"| DockerVol
    WSBuild --> WSMeta
    WSMeta -->|"запись"| PG

    WSMeta --> CodeGen
    CodeGen -->|"LLM"| LLMRouter
    CodeGen --> Artifacts
    Artifacts -->|"запись"| PG

    Artifacts --> TestRun
    TestRun --> SemanticReview
    SemanticReview -->|"LLM"| LLMRouter
    SemanticReview --> Report
    Report -->|"запись"| PG

    LLMRouter --> GuardrailCheck
    GuardrailCheck --> ProviderCall
    ProviderCall --> TokenCount
    TokenCount -->|"учёт"| PG
    TokenCount -->|"трассы"| MLF
    TokenCount -->|"метрики"| OTel

    OTel --> Prom
    Prom --> Graf

    Report -->|"PASS"| Result
    Report -->|"FAIL → оркестратор решает"| TaskRecord

    Result --> AGUI_Events

    style Input fill:#e3f2fd,stroke:#1565C0
    style Output fill:#c8e6c9,stroke:#2E7D32
    style Storage fill:#fff8e1,stroke:#F57F17
    style Telemetry fill:#fce4ec,stroke:#AD1457
```

## Что хранится где

| Данные | Куда | Формат | Хранение |
|---|---|---|---|
| Задачи (id, статус, вход, результат) | PostgreSQL | Реляционная схема | Постоянно |
| Спецификации задач (критерии, план тестов) | PostgreSQL | Реляционная схема | Постоянно |
| Артефакты (диффы, результаты тестов) | PostgreSQL | Реляционная схема | Постоянно |
| Отчёты ревью (вердикт, находки) | PostgreSQL | Реляционная схема | Постоянно |
| Решения супервизора | PostgreSQL | Реляционная схема | Постоянно |
| Метаданные окружений (возможности, Dockerfile) | PostgreSQL | JSONB | Постоянно |
| Конфигурация LLM-провайдеров (адреса, цены) | PostgreSQL | JSONB | Постоянно |
| Карточки агентов (A2A) | PostgreSQL | JSONB | Постоянно |
| Учёт токенов по запросам | PostgreSQL | Строки | Постоянно |
| Секреты (зашифрованные) | PostgreSQL | Зашифрованные | Постоянно |
| Правила (принудительная память) | PostgreSQL | Текст | Постоянно |
| Факты (индексируемая память) | PostgreSQL + pgvector | Текст + вектор | Постоянно |
| События AG-UI | PostgreSQL | JSONB | Постоянно |
| Журнал аудита | PostgreSQL | JSONB | Постоянно |
| Файлы окружений (код, зависимости, инструменты) | Docker Volume | Файлы | Время жизни окружения |
| Трассы OTel | OTel Collector → stdout | OTLP | Настраиваемо |
| Метрики Prometheus | Prometheus TSDB | Метрики | 15 дней |
| Трассы LLM-вызовов (промпт, ответ) | MLFlow | Артефакты | Постоянно |

**PostgreSQL -- единственное хранилище всех данных платформы.**

## Что логируется

| Событие | Уровень | Содержит | Не содержит |
|---|---|---|---|
| Создание задачи | INFO | task_id, краткое описание | Полный текст |
| Вызов A2A | INFO | агент, метод, task_id, trace_id | Содержимое |
| LLM-запрос | INFO | модель, провайдер, токены, стоимость, задержка | Промпт/ответ (опция) |
| LLM-запрос (отладка) | DEBUG | Полный промпт и ответ | Секреты (замаскированы) |
| Блокировка защитой | WARN | тип (инъекция/секрет), agent_id, task_id | Заблокированное содержимое |
| Изменение состояния агента | WARN | agent_id, старый/новый статус | - |
| Изменение состояния провайдера | WARN | provider_id, старый/новый статус | - |
| Переход состояния задачи | INFO | task_id, старый/новый статус | - |
| Ошибка авторизации | WARN | agent_id, endpoint, причина | Значение токена |
| Исчерпание бюджета | WARN | task_id, agent_id, потрачено, лимит | - |
| Сохранение правила/факта | INFO | scope, автор, тип | Содержимое (отладка) |

## Потоки данных между агентами

Потоки динамические -- оркестратор решает через LLM, кому и что передать. Типичные маршруты:

```mermaid
flowchart LR
    subgraph Planner["Planner"]
        PI["описание задачи"] --> PO["спека<br/>(критерии приёмки,<br/>план тестирования)"]
    end

    subgraph Bootstrap["Bootstrapper"]
        BI["спека"] --> BO["workspace_id<br/>возможности<br/>MCP-адреса"]
    end

    subgraph Work["Worker"]
        WI["спека<br/>workspace_id<br/>возможности"] --> WO["дифф кода<br/>тесты<br/>журналы"]
    end

    subgraph Review["Reviewer"]
        RI["спека<br/>артефакты<br/>workspace_id"] --> RO["вердикт<br/>находки<br/>доказательства"]
    end

    PO -->|"одобрение<br/>пользователем"| BI
    BO --> WI
    WO --> RI
    RO -->|"FAIL → обратная связь"| WI
    RO -->|"PASS"| Result["Результат пользователю"]

    style Planner fill:#f3e5f5,stroke:#6A1B9A
    style Bootstrap fill:#e3f2fd,stroke:#1565C0
    style Work fill:#fff8e1,stroke:#F57F17
    style Review fill:#e8f5e9,stroke:#2E7D32
```

Это типичный маршрут, не фиксированный пайплайн. Оркестратор может вызвать bootstrapper повторно по запросу worker-а (`input_required`), или вернуть запрос planner-у для уточнения спеки.
