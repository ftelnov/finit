# Workflow -- Выполнение задачи

Динамический workflow с LLM-driven маршрутизацией и разрешением `input_required`.

## Supervisor loop (основной цикл оркестратора)

```mermaid
flowchart TD
    User["Пользователь"]

    User -->|"Новая задача"| Decide
    User -->|"Ответ на запрос"| Decide

    Decide{"Оркестратор (LLM):<br/>что делать дальше?"}

    Decide -->|"Нужна спека"| CallPlanner["Planner"]
    Decide -->|"Нужно окружение"| CallBoot["Bootstrapper"]
    Decide -->|"Разработка"| CallWorker["Worker"]
    Decide -->|"Ревью"| CallReview["Reviewer"]

    CallPlanner --> Result{"Результат агента"}
    CallBoot --> Result
    CallWorker --> Result
    CallReview --> Result

    Result -->|"completed"| Decide
    Result -->|"failed"| Decide
    Result -->|"input_required:<br/>разрешимо агентом"| Decide

    Result -->|"input_required:<br/>критический"| Decide
    Decide -->|"RUN_FINISHED"| User
    Decide -->|"input_required:<br/>критический"| User
    Decide -->|"Эскалация<br/>(бюджет исчерпан)"| User

    style User fill:#fff3e0,stroke:#FF9800,stroke-width:2px
    style Decide fill:#e3f2fd,stroke:#1565C0,stroke-width:2px
```

## Happy path (sequence)

```mermaid
sequenceDiagram
    participant U as User
    participant W as WebUI
    participant O as Orchestrator (LLM)
    participant PL as Planner
    participant B as Bootstrapper
    participant WK as Worker
    participant R as Reviewer
    participant LR as LLM Router

    U->>W: Описание задачи
    W->>O: POST /api/tasks {input}
    O-->>W: AG-UI: RUN_STARTED

    Note over O: LLM: "нужна спека" → Planner
    O->>PL: A2A tasks/send {input}
    PL->>LR: LLM call (structured output)
    PL-->>O: {spec: acceptance_criteria, test_plan}

    Note over O,U: User approval checkpoint
    O-->>W: AG-UI: RUN_AWAITING_INPUT {spec}
    U->>W: Approve
    W->>O: POST /api/tasks/{id}/input {approved}

    Note over O: LLM: "workspace нужен" → Bootstrapper
    O->>B: A2A tasks/send {spec}
    B-->>O: {workspace_id, capabilities}

    Note over O: LLM: "разрабатывать" → Worker
    O->>WK: A2A tasks/sendSubscribe {spec, workspace_id}
    WK-->>O: A2A streaming: progress
    O-->>W: AG-UI: TEXT_MESSAGE_CONTENT
    WK-->>O: {artifacts: [code, tests]}

    Note over O: LLM: "нужен ревью" → Reviewer
    O->>R: A2A tasks/send {spec, artifacts, workspace_id}
    R-->>O: {verdict: PASS, report}

    O-->>W: AG-UI: RUN_FINISHED {diff + report}
```

## Dynamic routing: Worker `input_required`

```mermaid
sequenceDiagram
    participant O as Orchestrator (LLM)
    participant WK as Worker
    participant PL as Planner
    participant B as Bootstrapper
    participant U as User

    O->>WK: A2A tasks/send {spec, workspace_id}
    WK-->>O: input_required: "Нет protoc для генерации кода"

    Note over O: LLM анализирует запрос:<br/>"нужен инструмент → Bootstrapper"
    O->>B: A2A tasks/send {install: protoc, workspace_id}
    B-->>O: {updated capabilities}

    Note over O: LLM: "возвращаемся к Worker"
    O->>WK: A2A tasks/send {resume, updated_capabilities}
    WK-->>O: {artifacts: [code, tests]}

    Note over O,PL: Другой сценарий: уточнение спеки
    O->>WK: A2A tasks/send {spec, workspace_id}
    WK-->>O: input_required: "Какой формат ответа API?"

    Note over O: LLM: "уточнение спеки → Planner"
    O->>PL: A2A tasks/send {clarify: "формат ответа API", spec}
    PL-->>O: {updated_spec_section}

    O->>WK: A2A tasks/send {resume, clarification}
    WK-->>O: {artifacts}

    Note over O,U: Крайний случай: только user
    O->>WK: A2A tasks/send {spec, workspace_id}
    WK-->>O: input_required: "Требования противоречивы"

    Note over O: LLM: "ни один агент не может → User"
    O-->>U: AG-UI: RUN_AWAITING_INPUT {question}
    U-->>O: Response
    O->>WK: A2A tasks/send {resume, user_response}
```

## Task state machine

```mermaid
stateDiagram-v2
    [*] --> created: POST /api/tasks

    created --> running: Orchestrator starts supervisor loop

    running --> awaiting_input: Agent input_required or spec approval
    awaiting_input --> running: Input resolved (by agent or user)

    running --> completed: Reviewer PASS, LLM confirms done
    running --> failed: Unrecoverable error, retries exhausted
    running --> escalated: Budget exhausted or max iterations

    awaiting_input --> cancelled: User rejects/cancels
    running --> cancelled: User cancellation

    completed --> [*]
    failed --> [*]
    escalated --> [*]
    cancelled --> [*]
```

Состояние `running` включает все внутренние фазы (planning, bootstrapping, working, reviewing). Оркестратор динамически переключается между ними внутри supervisor loop. Это не фиксированные переходы -- LLM решает.

## Обработка ошибок LLM Router

```mermaid
flowchart TD
    Req["Входящий запрос от агента"]
    Auth{"JWT валиден?"}
    Guard{"Guardrails пройдены?"}
    Budget{"Бюджет не исчерпан?"}
    Route["Выбор провайдера"]
    Send["Отправка запроса"]
    Resp{"Ответ получен?"}
    Health{"Провайдер здоров?"}
    CB{"Circuit breaker<br/>открыт?"}
    Next{"Есть другой<br/>провайдер?"}

    Req --> Auth
    Auth -->|Нет| Reject401["401 Unauthorized"]
    Auth -->|Да| Guard
    Guard -->|Injection detected| Block403["403 Blocked<br/>+ audit log"]
    Guard -->|Secret found| Block403
    Guard -->|OK| Budget
    Budget -->|Исчерпан| Reject429["429 Budget Exhausted"]
    Budget -->|OK| Route

    Route --> CB
    CB -->|Открыт| Next
    CB -->|Закрыт| Send

    Send --> Resp
    Resp -->|Таймаут / 5xx| Health
    Health -->|"3+ fails"| OpenCB["Открыть circuit breaker<br/>(cooldown 30s)"]
    OpenCB --> Next
    Resp -->|429 Rate Limit| Backoff["Exponential backoff"]
    Backoff --> Send
    Resp -->|200 OK| Stream["SSE stream → Agent"]
    Health -->|"< 3 fails"| Next

    Next -->|Да| Route
    Next -->|Нет| Reject503["503 All Providers Down"]

    style Reject401 fill:#ffcdd2,stroke:#C62828
    style Block403 fill:#ffcdd2,stroke:#C62828
    style Reject429 fill:#fff3e0,stroke:#E65100
    style Reject503 fill:#ffcdd2,stroke:#C62828
    style Stream fill:#c8e6c9,stroke:#2E7D32
```
