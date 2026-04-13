# Системный дизайн Finit PoC

## 1. Обзор

Finit -- агентная платформа для автономной разработки ПО. Пользователь описывает задачу, система координирует агентов (bootstrapper, worker, reviewer) внутри изолированных workspace-ов и выдает отполированный результат.

MVP фокусируется на:

- **Единый протокол**: A2A (JSON-RPC 2.0) для inter-agent коммуникации
- **AG-UI** (SSE) для real-time отображения событий в WebUI
- **Собственный LLM Router**: маршрутизация, балансировка, трекинг токенов, guardrails
- **Workspace-based изоляция**: все агентные действия внутри Docker-контейнеров
- **Полная observability**: OpenTelemetry, Prometheus, Grafana, MLFlow

Диаграммы: [docs/diagrams/](diagrams/). Спецификации модулей: [docs/specs/](specs/).

---

## 2. Ключевые архитектурные решения

### ADR-1: A2A как единый протокол

Все inter-agent коммуникации через **A2A** (JSON-RPC 2.0 over HTTP). Агенты регистрируются через Agent Card (`/.well-known/agent.json`), оркестратор вызывает их через `tasks/send`. ACP и MCP -- точки расширения за рамками MVP.

**Обоснование:** A2A -- наиболее зрелый открытый протокол с полным lifecycle задач (submitted → working → input-required → completed/failed). Один протокол снижает сложность, ускоряет интеграцию.

### ADR-2: AG-UI для пользовательского интерфейса

Протокол AG-UI (Server-Sent Events) доставляет потоковые события из оркестратора в WebUI. Пользователь видит в реальном времени: прогресс задачи, действия агентов, LLM-вызовы, промежуточные результаты.

**Обоснование:** SSE проще WebSocket для однонаправленного потока событий. AG-UI стандартизирует типы событий (`RUN_STARTED`, `STEP_STARTED`, `TOOL_CALL_START` и т.д.), что делает UI предсказуемым и расширяемым.

### ADR-3: Собственный LLM Router

Все LLM-запросы от агентов проходят через единый LLM Router (OpenAI-compatible proxy). Router обеспечивает:

- Маршрутизацию по имени модели
- Балансировку (round-robin, weighted, latency-based, health-aware)
- Трекинг токенов и стоимости per-request и per-task
- Streaming passthrough (SSE)
- Guardrails (prompt injection detection, secret leakage prevention)
- Circuit breaker при недоступности провайдера

**Обоснование:** единая точка контроля LLM-расходов, качества и безопасности. Агенты не управляют LLM-подключениями напрямую. Смена провайдера или модели -- изменение конфигурации Router, а не агентов.

### ADR-4: Workspace как единица изоляции

Workspace = Docker volume + сгенерированный Dockerfile + metadata. Каждая задача выполняется в workspace. Bootstrapper создает и поддерживает workspace. Worker и reviewer запускаются в контейнерах с примонтированным workspace.

**Обоснование:** воспроизводимость. Workspace полностью описывает среду выполнения. Нет "локального состояния" на хосте -- все bookkeeping внутри системы. Пользователь свободен от управления окружением.

### ADR-5: LLM-driven Supervisor (Orchestrator)

Оркестратор -- LLM-driven supervisor. Он динамически решает, какому агенту передать управление, на основании текущего состояния задачи. Агенты не общаются друг с другом напрямую -- все коммуникации через оркестратор.

Ключевой механизм: агенты возвращают `input_required` когда не могут продолжить. Оркестратор анализирует запрос через LLM и маршрутизирует:

- Worker не понимает задачу → **Planner** (доуточнение спеки)
- Worker-у не хватает инструментов/интеграций → **Bootstrapper** (расширение workspace)
- Ни один агент не может разрешить → **User** (крайний случай)

**Обоснование:** без LLM-driven routing оркестратор -- это скрипт, а не мультиагентная система. Динамическая маршрутизация позволяет агентам кооперироваться: worker может запросить расширение окружения или уточнение спеки не останавливая весь пайплайн.

### ADR-6: Spec как обязательный артефакт

Спецификация -- обязательный артефакт задачи. Ревьюер проверяет результат **по спеке**, а не по описанию задачи. Без спеки у ревьюера нет якоря для оценки: что считать pass, а что fail. Пользователь одобряет спеку до начала исполнения.

**Обоснование:** спека как единственный source of truth для ревью предотвращает субъективные оценки и "коллюзию" (injection, скомпрометировавший worker, не распространяется на ревью).

---

## 3. Модули и их роли

| Модуль | Роль | Стек |
|---|---|---|
| **Orchestrator** | LLM-driven supervisor: динамическая маршрутизация между агентами, разрешение `input_required`, управление задачами, AG-UI SSE | Rust |
| **LLM Router** | OpenAI-compatible прокси к LLM-провайдерам, балансировка, трекинг, guardrails, secrets vault | Rust |
| **Planner** | Постановщик задачи: structured spec с acceptance criteria, уточнение по запросу worker/orchestrator | Python, A2A сервер |
| **Bootstrapper** | Управление workspace, подготовка окружения, установка зависимостей, MCP серверов, интеграций | Python, A2A сервер |
| **Worker** | LLM-агент: генерация кода и тестов по спеке через MCP tools в workspace | Python, A2A сервер |
| **Reviewer** | LLM-агент: evidence-based ревью по спеке через MCP tools в workspace (read-only) | Python, A2A сервер |
| **WebUI** | Real-time интерфейс пользователя, AG-UI SSE клиент | TypeScript, React |
| **PostgreSQL** | Persistent state: задачи, workspace metadata, agent registry, token usage, provider config | PostgreSQL 16 |
| **Prometheus** | Сбор метрик со всех сервисов | Prometheus |
| **Grafana** | Дашборды: LLM, pipeline, agent health | Grafana |
| **MLFlow** | Трассировка LLM-вызовов и агентных цепочек | MLFlow |
| **OTel Collector** | Сбор и экспорт телеметрии (traces, metrics, logs) | OpenTelemetry Collector |

---

## 4. Основной workflow выполнения задачи

### 4.1 Supervisor Loop

Оркестратор -- LLM-driven supervisor. Вместо фиксированного пайплайна он в цикле принимает решения на основании текущего состояния задачи:

```
loop:
  state = get_task_state()
  next_action = LLM.decide(state, available_agents, history)
  result = dispatch(next_action)
  if result.status == "input_required":
    resolution = LLM.route_input_required(result.request, state)
    → route to Planner | Bootstrapper | User
  if result.status == "completed" and all_phases_done:
    → deliver to user
```

### 4.2 Happy path

1. Пользователь отправляет задачу через WebUI
2. Orchestrator LLM → решение: нужна спека → **Planner**
3. Planner генерирует structured spec (acceptance criteria, тестовый план)
4. Orchestrator → **User approval**: спека через AG-UI `RUN_AWAITING_INPUT`
5. Orchestrator LLM → решение: workspace нужен → **Bootstrapper**
6. Bootstrapper подготавливает workspace, возвращает capabilities
7. Orchestrator LLM → решение: можно разрабатывать → **Worker**
8. Worker разрабатывает по спеке в sandbox
9. Orchestrator LLM → решение: нужен ревью → **Reviewer**
10. Reviewer проверяет **по спеке**, возвращает verdict + evidence
11. `PASS` → результат пользователю. `FAIL` → Orchestrator LLM решает что дальше.

### 4.3 Динамическая маршрутизация `input_required`

Когда агент возвращает `input_required`, оркестратор через LLM анализирует запрос и маршрутизирует:

| Запрос агента | Orchestrator LLM решение | Куда |
|---|---|---|
| Worker: "Непонятно, какой формат ответа API" | Нужно уточнение спеки | → **Planner** (доуточняет spec) |
| Worker: "Нет `protoc` для генерации кода из proto-файлов" | Нужен инструмент в workspace | → **Bootstrapper** (расширяет workspace) |
| Worker: "Нужен MCP сервер для GitVerse CI" | Нужна интеграция | → **Bootstrapper** (устанавливает MCP) |
| Worker: "Требования противоречивы, не могу продолжить" | Нужно решение пользователя | → **User** (AG-UI `RUN_AWAITING_INPUT`) |
| Reviewer: "Тесты требуют test database, нет в workspace" | Нужен инструмент | → **Bootstrapper** |
| Bootstrapper: "Не могу определить нужную версию Python" | Нужно уточнение | → **Planner** или → **User** |

После разрешения `input_required` управление возвращается агенту, который его выставил.

### 4.4 Принцип эскалации

```
Agent input_required
  → Orchestrator LLM: можно ли разрешить другим агентом?
    → Да → dispatch to agent → resolve → return to original agent
    → Нет → escalate to user (крайний случай)
```

Пользователь -- subscriber of last resort. Система старается разрешить все внутренне.

### 4.5 Спека как якорь доверия

Worker и reviewer работают по одной и той же спеке, одобренной пользователем. Reviewer не видит reasoning worker-а -- только спеку и артефакты. Это предотвращает "коллюзию" (injection в worker не распространяется на ревью).

Planner может дополнить спеку по запросу worker-а (через orchestrator), но это видно в истории и в AG-UI.

### 4.6 Error handling

| Ситуация | Детект | Реакция Orchestrator LLM |
|---|---|---|
| Agent `input_required` | A2A task status | LLM route: Planner / Bootstrapper / User |
| Agent `failed` | A2A task status | LLM: retry? other agent? escalate? |
| Reviewer verdict `FAIL` | Review report | LLM: send feedback to Worker, или escalate |
| LLM provider down | Router 503 | Router failover; если все down → pause task |
| Budget exhausted | Token counter | Hard stop → escalation к пользователю |
| Prompt injection | Guardrail 403 | Request blocked, audit log, alert |
| Secret в prompt | Aho-Corasick 403 | Request blocked, never reaches LLM |

Полная диаграмма workflow: [docs/diagrams/workflow.md](diagrams/workflow.md).

---

## 5. Коммуникационные протоколы

### 5.1 A2A (Agent-to-Agent)

Каждый агент -- HTTP-сервис, реализующий A2A спецификацию (JSON-RPC 2.0).

**Agent Card** (`/.well-known/agent.json`):

```json
{
  "name": "worker",
  "description": "Generates code and tests within workspace sandbox",
  "url": "http://worker:9002",
  "version": "1.0.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false
  },
  "skills": [
    {
      "id": "develop",
      "name": "Develop Code",
      "description": "Generate code and tests based on task description"
    }
  ],
  "securitySchemes": {
    "bearer": { "type": "http", "scheme": "bearer" }
  }
}
```

**Методы JSON-RPC:**

| Метод | Направление | Описание |
|---|---|---|
| `tasks/send` | Orchestrator → Agent | Отправить задачу агенту |
| `tasks/sendSubscribe` | Orchestrator → Agent | Отправить задачу + подписаться на стриминг |
| `tasks/get` | Orchestrator → Agent | Получить статус задачи |
| `tasks/cancel` | Orchestrator → Agent | Отменить задачу |

**Task lifecycle в A2A:**

```
submitted → working → [input-required] → completed | failed
```

При стриминге агент шлет `TaskStatusUpdateEvent` и `TaskArtifactUpdateEvent` по мере работы. Оркестратор транслирует их в AG-UI события.

### 5.2 AG-UI (Agent-to-UI)

SSE endpoint на оркестраторе: `GET /ag-ui/tasks/{id}/events`

| Событие | Данные | Когда |
|---|---|---|
| `RUN_STARTED` | `{task_id, timestamp}` | Задача принята в работу |
| `STEP_STARTED` | `{step: "spec\|bootstrap\|work\|review", agent_id}` | Начало фазы агента |
| `STEP_FINISHED` | `{step, status, duration_ms}` | Конец фазы |
| `RUN_AWAITING_INPUT` | `{input_type: "spec_approval", spec, options}` | Ожидание решения пользователя (одобрение спеки) |
| `TEXT_MESSAGE_START` | `{message_id, role: "agent"}` | Агент начал текстовое сообщение |
| `TEXT_MESSAGE_CONTENT` | `{message_id, delta: "..."}` | Фрагмент текста (стриминг) |
| `TEXT_MESSAGE_END` | `{message_id}` | Конец сообщения |
| `TOOL_CALL_START` | `{tool_call_id, tool_name}` | Агент вызывает инструмент/LLM |
| `TOOL_CALL_ARGS` | `{tool_call_id, delta: "..."}` | Аргументы (стриминг) |
| `TOOL_CALL_END` | `{tool_call_id, result_summary}` | Вызов завершен |
| `STATE_SNAPSHOT` | `{task_state}` | Полный снимок состояния задачи |
| `STATE_DELTA` | `{path, value}` | Инкрементальное обновление |
| `RUN_FINISHED` | `{result, artifacts}` | Задача завершена успешно |
| `RUN_ERROR` | `{error, context, recovery_options}` | Ошибка / эскалация |

**End-to-end streaming pipeline:**

```
Provider → LLM Router (SSE passthrough) → Agent → A2A streaming → Orchestrator → AG-UI SSE → WebUI
```

Пользователь видит LLM-генерацию в реальном времени, включая промежуточные рассуждения агентов.

### 5.3 LLM Router API

OpenAI-compatible endpoint: `POST /v1/chat/completions`

**Дополнительные заголовки:**

| Header | Описание |
|---|---|
| `Authorization: Bearer <jwt>` | JWT-токен агента |
| `X-Task-ID` | Привязка запроса к задаче для трекинга |
| `X-Agent-ID` | Идентификация вызывающего агента |
| `X-Request-Budget` | Лимит токенов на этот запрос |

**Management API:**

| Endpoint | Метод | Описание |
|---|---|---|
| `/v1/providers` | GET | Список зарегистрированных провайдеров |
| `/v1/providers` | POST | Регистрация нового провайдера |
| `/v1/providers/{id}` | PUT | Обновление провайдера (URL, вес, лимиты) |
| `/v1/providers/{id}` | DELETE | Удаление провайдера |
| `/v1/usage` | GET | Статистика: токены, стоимость, по агентам/задачам |
| `/v1/usage/tasks/{id}` | GET | Статистика по конкретной задаче |
| `/health` | GET | Health check Router |
| `/metrics` | GET | Prometheus метрики |

Подробная спецификация: [docs/specs/llm-router.md](specs/llm-router.md).

---

## 6. State / Memory / Context

### 6.1 Распределение данных по хранилищам

| Данные | Хранилище | TTL | Обоснование |
|---|---|---|---|
| Задачи (lifecycle, контекст, спека, артефакты) | PostgreSQL | Permanent | **Source of truth** для всего task state |
| Workspace metadata | PostgreSQL | Permanent | Воспроизводимость |
| Agent registry (Agent Cards) | PostgreSQL | Permanent | Конфигурация платформы |
| LLM provider config | PostgreSQL | Permanent | Конфигурация Router |
| Token usage / cost per request | PostgreSQL | Permanent | Биллинг, аналитика |
| Secrets | PostgreSQL (encrypted) | Permanent | Хранилище секретов |
| Память (правила, факты) | PostgreSQL + pgvector | Permanent | Долговременная память агентов |
| События AG-UI | PostgreSQL | Permanent | Переподключение, аудит |
| LLM traces | MLFlow | Permanent | Аналитика качества |

**Принцип:** PostgreSQL -- единственное хранилище всех данных платформы.

### 6.2 Task Context

Каждая задача имеет контекст, который передается между фазами:

```
TaskContext:
  task_id:          string
  workspace_id:     string
  input:            string              # исходное описание задачи
  spec:             Spec                # одобренная пользователем спека (якорь ревью)
  workspace_state:  WorkspaceCapabilities  # отчет bootstrapper
  artifacts:        []Artifact          # код, тесты, конфиги от worker
  review_history:   []ReviewReport      # история ревью (для итераций)
  budget:
    max_llm_calls:  int     (default: 50)
    max_tokens:     int     (default: 500_000)
    max_iterations: int     (default: 3)
    spent_tokens:   int
    spent_calls:    int
    spent_cost:     float
    current_iteration: int
```

### 6.3 Context Budget и Memory Policy

**Бюджет** -- жесткий лимит на ресурсы задачи. При исчерпании -- немедленная эскалация пользователю с полным контекстом (что было сделано, почему не получилось).

**Memory policy:**

- **В рамках задачи:** полный контекст передаётся между агентами через оркестратор (PostgreSQL). Worker видит возможности окружения. Reviewer видит артефакты worker-а. Каждая итерация видит обратную связь предыдущей.
- **Inter-task:** workspace (Docker volume) персистится между задачами. Установленные bootstrapper-ом инструменты и зависимости доступны для будущих задач в том же workspace.
- **LLM context window:** каждый агент самостоятельно управляет контекстным окном LLM (промпт + history). Orchestrator не контролирует содержимое промптов агентов, только бюджет токенов.

---

## 7. Workspace и изоляция

### 7.1 Workspace Model

```
Workspace:
  id:               string           # "ws-{hash}"
  project_id:       string           # привязка к проекту
  base_image:       string           # e.g., "finit/workspace-go:1.22"
  dockerfile:       string           # сгенерированный bootstrapper
  volume_name:      string           # Docker volume
  tools:            []InstalledTool   # версии, пути
  dependencies:     []Dependency      # проектные зависимости
  network_policy:   NetworkPolicy     # egress allowlist
  status:           "building" | "ready" | "failed" | "archived"
  created_at:       timestamp
  last_used_at:     timestamp
```

### 7.2 Контейнерная изоляция

Система различает два уровня контейнеров:

1. **A2A agent services** -- долгоживущие сервисы (bootstrapper, worker, reviewer), поднятые через Docker Compose. Имеют нормальный сетевой доступ для A2A вызовов и LLM Router.
2. **Per-task sandbox** -- эфемерная изолированная среда, в которой агенты исполняют код и тесты внутри workspace. Изоляция (network, resources, kernel) применяется здесь.

**Целевая модель изоляции: Firecracker microVM.**

Firecracker обеспечивает полную VM-level изоляцию: каждый sandbox получает выделенное ядро Linux через KVM. Это позволяет воспроизводить произвольное рабочее окружение (OS, пакеты, конфигурации) без риска escape из sandbox. В MVP используется Docker как fallback; миграция на Firecracker -- эволюционная.

| Уровень | Sandbox backend | Изоляция | Когда |
|---|---|---|---|
| **MVP** | Docker containers | Namespace + cgroups | Разработка, тестирование |
| **Production** | Firecracker microVM | Выделенное ядро (KVM) | Продакшен, < 150ms boot |

```
Host
  └── Docker Engine (MVP) / Firecracker (production)
       │
       │   Platform services (Docker Compose):
       ├── orchestrator              (bridge network, management)
       ├── llm-router                (bridge network, proxy)
       ├── agent-planner             (bridge network, A2A + LLM Router)
       ├── agent-bootstrapper        (bridge network, Docker socket: ro)
       ├── agent-worker              (bridge network, A2A + LLM Router)
       ├── agent-reviewer            (bridge network, A2A + LLM Router)
       │
       │   Ephemeral workspaces (created by bootstrapper):
       │
       ├── workspace-{task-id}       (isolated network, MCP servers inside)
       │    ├── project volume (/workspace, rw)
       │    ├── MCP servers: file_rw, bash_exec, test_run, lint
       │    ├── secrets: mounted as env/file
       │    └── resources: CPU 2 cores, RAM 4GB, PIDs 256
       │
       └── workspace-review-{task-id} (no network)
            ├── project volume (/workspace, ro)
            ├── MCP servers: file_read, bash_exec (ro), test_run, lint
            ├── secrets: not mounted
            └── resources: CPU 1 core, RAM 2GB, PIDs 128
```

**Ключевые принципы:**

- **Bootstrapper** создает и провиженит workspace: устанавливает зависимости, запускает MCP серверы, монтирует секреты
- **Worker и reviewer** -- LLM-агенты, вызывающие MCP tools внутри workspace. Они не управляют контейнерами.
- Worker workspace: read-write, секреты доступны
- Reviewer workspace: read-only, без секретов
- Firecracker: полная VM-изоляция позволяет воспроизводить любой workflow (произвольная ОС, rootfs, сеть)

Подробная спецификация: [docs/specs/workspace.md](specs/workspace.md).

---

## 8. LLM Router

### 8.1 Архитектура Router

```
Agent Request
    ↓
[Auth Middleware]  → reject if invalid JWT
    ↓
[Guardrails]       → block prompt injection / secret leakage
    ↓
[Budget Check]     → reject if task budget exhausted
    ↓
[Model Router]     → select provider pool by model name
    ↓
[Load Balancer]    → select specific provider instance
    ↓
[Provider Call]    → forward request, stream response
    ↓
[Token Counter]    → count input/output tokens, calculate cost
    ↓
[Metrics Emit]     → TTFT, TPOT, tokens, cost, status → OTel
    ↓
[MLFlow Log]       → prompt, response, metadata → MLFlow
    ↓
Agent Response (streamed SSE)
```

### 8.2 Стратегии балансировки

| Стратегия | Описание | Применение |
|---|---|---|
| **model-route** | Маршрутизация по имени модели в запросе | Всегда (первый уровень) |
| **round-robin** | Циклическое распределение по инстансам | Одинаковые реплики одной модели |
| **weighted** | Статические веса на провайдеров | Приоритизация дешевого/быстрого |
| **latency-based** | Приоритет провайдеру с наименьшим EWMA latency | Автоматическая оптимизация |
| **health-aware** | Исключение нездоровых провайдеров из пула | 3+ ошибки за 30s → cooldown 30s |

Стратегии комбинируются: model-route → health-aware filter → latency-based / weighted selection.

### 8.3 Динамическая регистрация провайдеров

```json
POST /v1/providers
{
  "name": "vllm-local",
  "url": "http://localhost:8000/v1",
  "api_key": "...",
  "weight": 10,
  "priority": 1,
  "rate_limit": { "rpm": 60, "tpm": 100000 },
  "models": {
    "qwen3-72b": {
      "pricing": { "input_per_1m_tokens": 0.0, "output_per_1m_tokens": 0.0 }
    },
    "deepseek-v3": {
      "pricing": { "input_per_1m_tokens": 0.27, "output_per_1m_tokens": 1.10 }
    }
  },
  "health_check_interval_s": 10
}
```

Провайдеры добавляются/удаляются без перезапуска Router.

### 8.4 Guardrails

Реализованы как middleware-цепочка в LLM Router:

1. **Prompt Injection Detection**: паттерны (regex) + эвристики на структурные атаки (role injection, instruction override). Конфигурируемый порог.
2. **Secret Leakage Prevention**: Aho-Corasick multi-pattern scan по всем известным secret values. O(n) по длине prompt, независимо от количества секретов.
3. **Token Budget Enforcement**: проверка `X-Task-ID` → remaining budget. Reject если бюджет исчерпан.
4. **Output Validation**: structured output JSON schema enforcement на ответ LLM.

### 8.5 Семантическое кэширование LLM-ответов

LLM Router поддерживает семантический кэш для повторяющихся запросов. Цель -- сократить латентность и стоимость для идентичных или семантически близких промптов (типовые вопросы агентов, повторная генерация boilerplate-кода).

**Архитектура кэша:**

```
Agent Request
    ↓
[Guardrails]  → pass
    ↓
[Semantic Cache Lookup]
    ↓ cache hit (similarity ≥ threshold)
    → return cached response (skip provider call)
    ↓ cache miss
[Provider Call] → response
    ↓
[Cache Store] → save embedding + response
    ↓
Agent Response
```

**Реализация:**

| Параметр | Значение | Обоснование |
|---|---|---|
| Хранилище | PostgreSQL + pgvector | Единая БД, уже используется для фактов |
| Embedding-модель | `all-MiniLM-L6-v2` (384d) | Та же модель, что и для памяти фактов — нет дополнительного overhead |
| Порог схожести | 0.95 cosine similarity (конфигурируемый) | Высокий порог для минимизации ложных попаданий |
| Ключ кэша | Embedding конкатенации user-сообщений (без system prompt) | System prompt стабилен, различия — в пользовательских сообщениях |
| Scope | Per-model, per-agent | Разные модели и агенты — разные кэш-бакеты |
| TTL | 24 часа (конфигурируемый) | Баланс между актуальностью и экономией |
| Инвалидация | TTL + ручной flush через Management API | `DELETE /v1/cache` — очистка; `DELETE /v1/cache?model=X` — по модели |
| Когда НЕ кэшировать | `temperature > 0`, заголовок `X-No-Cache: true`, streaming-запросы | Недетерминированные и потоковые запросы не кэшируются |

**Метрики кэша:**

| Метрика | Тип | Описание |
|---|---|---|
| `finit_llm_cache_hits_total` | counter | Попадания в кэш |
| `finit_llm_cache_misses_total` | counter | Промахи кэша |
| `finit_llm_cache_hit_ratio` | gauge | Hit rate за последние 5 минут |
| `finit_llm_cache_latency_seconds` | histogram | Латентность lookup (embedding + search) |
| `finit_llm_cache_size` | gauge | Количество записей в кэше |

**Схема PostgreSQL:**

```sql
CREATE TABLE llm_cache (
    id              SERIAL PRIMARY KEY,
    model           TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    messages_hash   TEXT NOT NULL,          -- SHA256 для быстрого exact match
    embedding       vector(384) NOT NULL,   -- для semantic match
    response        JSONB NOT NULL,
    tokens_saved    INT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_llm_cache_exact ON llm_cache(model, agent_id, messages_hash);
CREATE INDEX idx_llm_cache_semantic ON llm_cache USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX idx_llm_cache_expiry ON llm_cache(expires_at);
```

Lookup выполняется в два этапа: сначала exact match по SHA256 hash (O(1)), затем semantic search по embedding если exact miss (IVFFlat index).

Подробная спецификация: [docs/specs/llm-router.md](specs/llm-router.md).

---

## 9. Observability

### 9.1 OpenTelemetry

Все сервисы инструментированы OTel SDK. Телеметрия экспортируется в OTel Collector, который распределяет данные:

- **Traces** → Jaeger / stdout (distributed tracing)
- **Metrics** → Prometheus
- **Logs** → structured JSON → stdout (собираются Docker)

**Trace propagation через A2A:**

```
User Request [trace_id=abc]
  └── Orchestrator span
       ├── A2A call: Bootstrapper span
       │    └── LLM call span (via Router)
       ├── A2A call: Worker span
       │    ├── LLM call span 1
       │    ├── LLM call span 2
       │    └── Tool exec span (bash, file write)
       └── A2A call: Reviewer span
            ├── Test execution span
            └── LLM call span
```

`trace_id` и `span_id` передаются через HTTP заголовки `traceparent` (W3C Trace Context) в A2A вызовах и LLM Router запросах.

### 9.2 Prometheus метрики

| Метрика | Тип | Labels |
|---|---|---|
| `finit_llm_requests_total` | counter | `provider`, `model`, `status`, `agent` |
| `finit_llm_ttft_seconds` | histogram | `provider`, `model` |
| `finit_llm_tpot_seconds` | histogram | `provider`, `model` |
| `finit_llm_tokens_total` | counter | `provider`, `model`, `direction` (input/output) |
| `finit_llm_cost_dollars` | counter | `provider`, `model` |
| `finit_llm_provider_health` | gauge | `provider` (1=healthy, 0=unhealthy) |
| `finit_llm_provider_latency_ewma_seconds` | gauge | `provider`, `model` |
| `finit_llm_request_duration_seconds` | histogram | `provider`, `model`, `status` |
| `finit_llm_active_requests` | gauge | `provider` |
| `finit_task_total` | counter | `status` (completed/failed/escalated) |
| `finit_task_duration_seconds` | histogram | `phase` (bootstrap/work/review/total) |
| `finit_task_iterations_total` | histogram | - |
| `finit_agent_invocations_total` | counter | `agent`, `status`, `method` |
| `finit_agent_duration_seconds` | histogram | `agent`, `method` |
| `finit_agent_health` | gauge | `agent` (1=healthy, 0=unhealthy) |
| `finit_workspace_build_duration_seconds` | histogram | `base_image` |
| `finit_guardrail_checks_total` | counter | `type`, `result` (passed/blocked) |
| `finit_guardrail_blocks_total` | counter | `type` (injection/secret/budget) |

### 9.3 Grafana дашборды

1. **LLM Overview**: latency distribution (TTFT/TPOT), throughput, cost accumulation, provider health, traffic per provider
2. **Task Pipeline**: task flow, phase durations, success/fail/escalation rates, iteration counts
3. **Agent Health**: per-agent invocation rates, error rates, latency, resource usage (CPU/RAM)

### 9.4 MLFlow

Каждая задача = MLFlow **experiment run**. Внутри:

- Каждый LLM-вызов = logged artifact с: полный prompt, response, tokens (in/out), latency, model, temperature, provider
- Агентные фазы = nested runs (bootstrap → work → review)
- Метрики качества: review pass rate, iteration count, budget utilization

MLFlow трекинг инициируется LLM Router: каждый proxied request автоматически логируется.

### 9.5 Версионирование промптов

Промпты агентов эволюционируют. Стратегия версионирования обеспечивает отслеживание, сравнение и безопасное обновление промптов.

**Три уровня версионирования:**

| Уровень | Что версионируется | Где хранится | Как обновляется |
|---|---|---|---|
| **Шаблоны промптов** | Системные промпты агентов, structured output JSON schemas | Git-репозиторий (`prompts/v{N}/`) | Коммит → новая версия |
| **Runtime-конфигурация** | Активная версия промпта per-agent, параметры (temperature, top_p) | PostgreSQL `prompt_configs` | API или конфиг |
| **Аудит вызовов** | Полный prompt + response + версия шаблона | MLFlow artifacts | Автоматически LLM Router |

**Структура хранения шаблонов:**

```
prompts/
├── planner/
│   ├── v1/
│   │   ├── system.md
│   │   └── schema.json
│   └── v2/
│       ├── system.md
│       ├── schema.json
│       └── CHANGELOG.md
├── worker/
│   └── v1/ ...
└── reviewer/
    └── v1/ ...
```

**A/B тестирование промптов:**

Router поддерживает маршрутизацию по версии промпта через конфигурацию:

```yaml
prompt_routing:
  planner:
    versions:
      - version: "v1"
        weight: 80       # 80% трафика
      - version: "v2"
        weight: 20       # 20% трафика
    metrics_window: "24h" # окно сравнения
```

Каждый LLM-вызов логируется в MLFlow с тегом `prompt_version`. Сравнение метрик:

- **Review pass rate** per prompt version
- **Iteration count** per prompt version (меньше итераций = лучше промпт)
- **Token usage** per prompt version (эффективность)
- **Structured output validity** per prompt version

**Canary rollout:**

```
1. Новая версия промпта коммитится в git (prompts/{agent}/v{N+1}/)
2. Конфигурация: weight=5 для новой версии, weight=95 для текущей
3. Мониторинг 24-48h: сравнение метрик в Grafana (дашборд "Prompt Versions")
4. При деградации: вес новой версии → 0, откат мгновенный (без деплоя)
5. При успехе: постепенное увеличение веса (5 → 25 → 50 → 100)
6. Полный rollout: старая версия архивируется
```

**Таблица PostgreSQL:**

```sql
CREATE TABLE prompt_configs (
    id              SERIAL PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    version         TEXT NOT NULL,           -- "v1", "v2"
    weight          INT NOT NULL DEFAULT 100, -- вес для A/B
    template_path   TEXT NOT NULL,           -- путь в git
    parameters      JSONB DEFAULT '{}',     -- temperature, top_p, etc.
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent_id, version)
);
```

### 9.6 Health checks

Каждый сервис реализует `GET /health`:

| Сервис | Проверяет |
|---|---|
| Orchestrator | PostgreSQL connectivity |
| LLM Router | >= 1 healthy provider |
| Bootstrapper | Docker socket доступен |
| Worker | Workspace volume примонтирован |
| Reviewer | Workspace volume примонтирован |

Подробная спецификация: [docs/specs/observability.md](specs/observability.md).

---

## 10. Авторизация

### 10.1 JWT-based agent auth

Каждый агент при регистрации получает JWT-токен, подписанный оркестратором:

```json
{
  "sub": "agent:worker",
  "iss": "finit-orchestrator",
  "scopes": ["workspace:rw", "task:rw"],
  "budget": { "max_tokens_per_task": 200000 },
  "iat": 1711929600,
  "exp": 1712016000
}
```

JWT используется:
- В A2A вызовах (Orchestrator → Agent): Bearer token
- В LLM Router запросах (Agent → Router): Bearer token для идентификации и авторизации

### 10.2 Scopes

Два домена доступа: workspace и task state. Секреты управляются через workspace mount API (только orchestrator), агенты не имеют прямого доступа к значениям секретов.

| Scope | Описание | planner | bootstrapper | worker | reviewer |
|---|---|---|---|---|---|
| `workspace:rw` | Чтение, запись, монтирование секретов в workspace | - | + | + | - |
| `workspace:ro` | Только чтение workspace | + | - | - | + |
| `task:rw` | Чтение и обновление состояния задачи | + | + | + | + |
| `task:ro` | Только чтение состояния задачи | - | - | - | - |

---

## 11. Управление секретами (Platform Primitive)

Секреты -- платформенный примитив уровня workspace. Агенты **не имеют API для чтения секретов**. Единственный способ использования -- монтирование в workspace sandbox как файл или env variable.

### 11.1 Модель: secrets = workspace mounts

```
Secrets Store (PostgreSQL, encrypted)
    ↓
Workspace API: mount_secret(key, target)
    ↓
Sandbox container получает секрет как:
    - env var:  GITHUB_TOKEN=ghp_xxx...
    - file:     /workspace/.secrets/github_token
```

**Чего нет:**
- Нет `GET /v1/secrets/{key}` API. Агент не может прочитать значение секрета программно.
- Нет передачи секретов в A2A messages или task context.
- Нет доступа к секретам через LLM prompts.

**Что есть:**
- Orchestrator / Bootstrapper монтируют секреты в sandbox при его создании.
- Код, исполняемый внутри sandbox, использует секреты неявно (`git push` читает `GITHUB_TOKEN` из env, но агент-worker не знает его значение на уровне своего LLM-reasoning).

### 11.2 Workspace Secrets API

| Endpoint | Method | Описание |
|---|---|---|
| `PUT /v1/secrets/{key}` | PUT | Сохранить секрет в store (admin) |
| `DELETE /v1/secrets/{key}` | DELETE | Удалить секрет (admin) |
| `GET /v1/secrets` | GET | Список ключей (без значений, admin) |
| `POST /v1/workspaces/{id}/secrets` | POST | Примонтировать секрет в workspace |

Монтирование:

```json
POST /v1/workspaces/ws-abc/secrets
{
  "key": "GITHUB_TOKEN",
  "mount_as": "env",           // "env" | "file"
  "target": "GITHUB_TOKEN"    // env var name or file path
}
```

### 11.3 LLM Firewall

LLM Router сканирует **все** исходящие промпты на наличие known secret values:

- **Aho-Corasick** multi-pattern matcher: O(n) scan по всему prompt, независимо от количества секретов
- При обнаружении: запрос **блокируется**, событие логируется (key, не value), агент получает 403
- Минимальная длина паттерна: 8 символов (предотвращение false positives)
- Паттерны перестраиваются при обновлении secrets store

Это последний рубеж: даже если агент каким-то образом прочитал файл секрета в sandbox и попытался отправить его в LLM -- firewall заблокирует.

### 11.4 Audit

Каждая операция с секретами логируется: `key` (не value), `action` (create/delete/mount/firewall_block), `agent_id`, `task_id`, `workspace_id`, `timestamp`.

---

## 12. Failure modes, fallback и guardrails

### 12.1 Таблица failure modes

| Failure Mode | Probability | Impact | Detection | Recovery | Guardrail |
|---|---|---|---|---|---|
| LLM provider полностью недоступен | Средняя | Высокий | Health check fail | Failover к другому провайдеру | Circuit breaker: 3 fails → 30s cooldown |
| LLM rate limit (429) | Высокая | Средний | HTTP 429 | Exponential backoff + jitter | Per-provider rate limiter |
| LLM hallucination | Средняя | Высокий | JSON schema validation fail | Retry до 3x | Structured output enforcement |
| Prompt injection | Низкая | Высокий | Guardrail regex + heuristics | Block request, audit log | Middleware в Router |
| Secret leakage в prompt | Низкая | Критический | Aho-Corasick scan | Block request, alert | Middleware в Router |
| Agent container crash | Низкая | Средний | Docker exit code != 0 | Restart + retry | Max 2 restarts per task |
| Workspace build failure | Средняя | Средний | Bootstrapper A2A status=failed | Retry с fallback image | Max 2 retries |
| Infinite review loop | Средняя | Средний | Iteration counter | Hard stop после N iterations | max_iterations=3 |
| Budget exhaustion | Средняя | Низкий | Token counter | Эскалация к пользователю | Hard limit per task |
| PostgreSQL down | Низкая | Критический | Health check | Wait + exponential backoff | Orchestrator pauses |
| Network partition с agent | Низкая | Средний | A2A timeout | Retry → restart container | Timeout: 30s |

### 12.2 Каскадный fallback LLM Router

```
Primary Provider (latency-based)
    ↓ fail/timeout
Secondary Provider (next by weight)
    ↓ fail/timeout
Tertiary Provider / Mock LLM
    ↓ all providers down
503 Service Unavailable → Orchestrator pauses task
```

---

## 13. Технические и операционные ограничения

### 13.1 SLO (Service Level Objectives)

Целевые показатели формализованы как SLO с привязкой к алертам Prometheus. Каждый SLO имеет alert threshold, при пересечении которого срабатывает оповещение.

| SLO ID | Метрика | Цель | Alert threshold | Окно | Severity |
|---|---|---|---|---|---|
| SLO-1 | Workspace build time (p95) | < 60s | p95 > 90s за 15m | 15 min | warning |
| SLO-2 | A2A round-trip latency (p99) | < 100ms | p99 > 200ms за 5m | 5 min | warning |
| SLO-3 | AG-UI event delivery latency | < 200ms | p95 > 500ms за 5m | 5 min | warning |
| SLO-4 | LLM Router availability | 99.5% | error rate > 5% за 5m | 5 min | critical |
| SLO-5 | Task completion rate | > 90% | completion rate < 80% за 1h | 1 hour | warning |
| SLO-6 | LLM provider failover time | < 1s | failover > 3s | per-event | critical |
| SLO-7 | Guardrail false negative rate | 0% (по секретам) | Любая утечка секрета | per-event | critical |
| SLO-8 | Agent health uptime | 99.9% | agent unhealthy > 60s | 1 min | critical |

**Prometheus alert rules** (см. полную конфигурацию в [docs/specs/observability.md](specs/observability.md)):

```yaml
groups:
  - name: finit-slo
    rules:
      # SLO-1: Workspace build time
      - alert: WorkspaceBuildSlow
        expr: histogram_quantile(0.95, rate(finit_workspace_build_duration_seconds_bucket[15m])) > 90
        for: 5m
        labels:
          severity: warning
          slo: SLO-1
        annotations:
          summary: "Workspace build p95 > 90s"

      # SLO-4: LLM Router availability
      - alert: LLMRouterHighErrorRate
        expr: |
          rate(finit_llm_requests_total{status=~"5.."}[5m])
          / rate(finit_llm_requests_total[5m]) > 0.05
        for: 2m
        labels:
          severity: critical
          slo: SLO-4
        annotations:
          summary: "LLM Router error rate > 5%"

      # SLO-6: Provider failover
      - alert: ProviderFailoverSlow
        expr: finit_llm_circuit_breaker_state > 0 and ON() increase(finit_llm_requests_total{status="503"}[1m]) > 0
        for: 3s
        labels:
          severity: critical
          slo: SLO-6

      # SLO-8: Agent health
      - alert: AgentUnhealthy
        expr: finit_agent_health == 0
        for: 60s
        labels:
          severity: critical
          slo: SLO-8
        annotations:
          summary: "Agent {{ $labels.agent }} unhealthy > 60s"
```

**Эскалация алертов:**

| Severity | Канал | Действие |
|---|---|---|
| `warning` | Grafana dashboard annotation | Визуальная индикация, без push-уведомления |
| `critical` | Webhook → (Telegram / Slack / email) | Push-уведомление оператору |
| `critical` (> 5 min unresolved) | Автоматическая пауза задач | Оркестратор приостанавливает новые задачи |

### 13.2 Операционные лимиты

| Метрика | Значение | Обоснование |
|---|---|---|
| Concurrent tasks | 5 | Ресурсы одной машины |
| Max task duration | 30 min | Предотвращение зависаний |
| Max LLM calls per task | 50 | Контроль расходов |
| Max tokens per task | 500K | Контроль расходов |
| Max review iterations | 3 | Предотвращение бесконечных циклов |

### 13.3 Операционные ограничения

- **Single machine**: все компоненты на одном хосте (32GB+ RAM, GPU для vLLM/Ollama)
- **Docker**: Docker Desktop (macOS) / Docker Engine (Linux). Без Firecracker/Kata в MVP
- **LLM**: только OpenAI-compatible API (vLLM, Ollama, OpenAI, Anthropic, mock)
- **Нет distributed mode**: PostgreSQL, все агенты -- локально
