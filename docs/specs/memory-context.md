# Спецификация: Состояние, контекст и память

## Назначение

Управление состоянием задач, рабочих окружений, платформы и долговременной памятью агентов. Всё персистентное состояние -- в PostgreSQL.

---

## 1. Долговременная память

Агенты накапливают знания о задачах и окружениях в виде фрагментов памяти. Два типа:

### 1.1 Правила (rules)

**Гарантированно попадают в промпт** агента при работе с соответствующей задачей или окружением. Это принудительная память -- агент не может её проигнорировать.

Примеры:
- "В этом проекте используется chi v5, не net/http напрямую"
- "Тесты запускать с флагом `-race`"
- "Не трогать файл `legacy/auth.go` -- он заморожен"
- "API возвращает ошибки в формате `{\"error\": {\"code\": ..., \"message\": ...}}`"

Правила привязаны к области:

| Область | Когда попадает в промпт | Кто создаёт |
|---|---|---|
| `workspace:{id}` | При любой работе в этом окружении | Bootstrapper, worker, пользователь |
| `task:{id}` | При работе над этой задачей | Planner, worker, reviewer, пользователь |
| `global` | Всегда | Пользователь, администратор |

Правила компактны (рекомендация: < 200 токенов каждое). Оркестратор собирает все применимые правила и включает их в промпт агента перед вызовом.

### 1.2 Факты (facts)

**Извлекаются по запросу через семантический поиск.** Это индексируемое хранилище наблюдений, которые агенты записывают в процессе работы. Не попадают в промпт автоматически -- агент или оркестратор запрашивает релевантные факты по необходимости.

Примеры:
- "Файл `handler.go` содержит 12 эндпоинтов, последний добавлен в итерации 2"
- "Зависимость `chi` обновлена до v5.0.12 в задаче task-456"
- "Тесты в `pkg/auth` нестабильны -- 2 из 5 запусков падают на CI"
- "MCP-сервер gitverse-ci требует переменную `GITVERSE_TOKEN`"

Факты привязаны к области аналогично правилам (`workspace`, `task`, `global`).

### 1.3 Жизненный цикл

```
Агент обнаруживает что-то важное
    ↓
Вызывает Memory API: save_rule(...) или save_fact(...)
    ↓
PostgreSQL + векторный индекс (для фактов)
    ↓
При следующем вызове агента:
  - Правила: оркестратор собирает по scope, вставляет в промпт
  - Факты: агент/оркестратор делает semantic_search по необходимости
```

### 1.4 API памяти

Доступно всем агентам через платформу:

```
Memory.save_rule(scope, content) → rule_id
Memory.save_fact(scope, content, tags[]) → fact_id
Memory.search_facts(query, scope?, limit) → Fact[]
Memory.list_rules(scope) → Rule[]
Memory.delete(id)
Memory.update(id, content)
```

### 1.5 Сборка промпта (оркестратор)

При вызове агента оркестратор формирует промпт:

```
1. Системный промпт агента (статический)
2. [ПРАВИЛА] Все rules для scope=global
3. [ПРАВИЛА] Все rules для scope=workspace:{id} (если есть)
4. [ПРАВИЛА] Все rules для scope=task:{id}
5. Контекст задачи (спека, артефакты, обратная связь)
6. [ФАКТЫ] Результаты semantic_search по текущей подзадаче (опционально)
```

Правила занимают фиксированную часть контекстного окна. Если суммарный объём правил превышает бюджет (например, 2K токенов), оркестратор уведомляет пользователя -- правил слишком много, нужно вычистить.

---

## 2. Уровни хранения состояния

### 2.1 Контекст задачи

Живёт в рамках одной задачи. Полностью в PostgreSQL (реляционная схема).

### 2.2 Состояние рабочего окружения

Живёт между задачами одного проекта. Метаданные в PostgreSQL, файлы в Docker Volume.

```
Docker Volume:
  /workspace/project/    # исходный код
  /workspace/tools/      # инструменты
  /workspace/.finit/     # метаданные окружения

PostgreSQL:
  workspaces.*           # возможности, инструменты, Dockerfile
  memory_rules           # правила для этого окружения
  memory_facts           # факты об этом окружении
```

### 2.3 Состояние платформы

Живёт всегда. PostgreSQL.

```
- Реестр агентов (карточки, JWT, состояние)
- Конфигурация LLM-провайдеров (адреса, цены, веса)
- История использования токенов
- История задач
- Журнал аудита
- Глобальные правила и факты
```

---

## 3. Бюджет задачи

Каждая задача имеет бюджет, контролируемый оркестратором:

```json
{
  "max_tokens": 500000,
  "max_llm_calls": 50,
  "max_iterations": 3,
  "max_duration_minutes": 30,
  "spent_tokens": 0,
  "spent_calls": 0,
  "spent_cost_dollars": 0.0,
  "current_iteration": 0,
  "started_at": "2026-04-01T10:00:00Z"
}
```

### Контроль бюджета

| Точка проверки | Кто проверяет | Действие при превышении |
|---|---|---|
| LLM-запрос | LLM Router (через X-Task-ID) | 429 — бюджет исчерпан |
| Начало итерации | Оркестратор | Эскалация пользователю |
| Время выполнения | Оркестратор (таймер) | Отмена задачи, эскалация |

### Обновление бюджета

```
Агент → LLM-вызов → LLM Router
                       ↓
                Подсчёт токенов + стоимость
                       ↓
                UPDATE task_budgets
                SET spent_tokens = spent_tokens + N,
                    spent_calls = spent_calls + 1,
                    spent_cost = spent_cost + C
                WHERE task_id = ?
```

---

## 4. Управление контекстным окном

Каждый агент управляет своим контекстным окном самостоятельно. Оркестратор отвечает за включение правил и запрос фактов.

### Структура промпта

```
┌─────────────────────────────────────────┐
│ Системный промпт агента       (~500 т.) │
├─────────────────────────────────────────┤
│ [ПРАВИЛА] global + workspace + task     │
│ (принудительно, всегда)       (~1-2K т.)│
├─────────────────────────────────────────┤
│ Контекст задачи: спека, артефакты,      │
│ обратная связь, возможности окружения   │
│                               (~4-8K т.)│
├─────────────────────────────────────────┤
│ [ФАКТЫ] релевантные, по запросу         │
│ (семантический поиск)         (~1-2K т.)│
├─────────────────────────────────────────┤
│ Текущий запрос / подзадача              │
└─────────────────────────────────────────┘
```

### Сжатие контекста

При приближении к пределу:

1. **Сводка предыдущих итераций**: вместо полной истории -- краткое изложение
2. **Усечение кода**: показать только изменённые файлы
3. **Ссылки на артефакты**: пути к файлам в окружении вместо содержимого
4. **Факты опускаются первыми** -- правила не урезаются

---

## 5. Передача данных между агентами

### Planner → Пользователь → Оркестратор

```json
{
  "task_id": "task-123",
  "title": "Добавить эндпоинт проверки работоспособности",
  "description": "Добавить GET /healthz, возвращающий 200 OK с аптаймом",
  "acceptance_criteria": [
    "GET /healthz возвращает 200 с JSON {\"status\": \"ok\", \"uptime_seconds\": <int>}",
    "Аптайм считается от момента запуска сервера",
    "Эндпоинт зарегистрирован на основном маршрутизаторе",
    "Модульный тест покрывает формат ответа и код статуса"
  ],
  "test_plan": {
    "unit_tests": ["TestHealthzReturns200", "TestHealthzResponseFormat"],
    "commands": ["go test ./... -v -run TestHealthz"]
  },
  "files_likely_affected": ["handler.go", "handler_test.go", "main.go"],
  "domains": ["go-backend"]
}
```

Спека -- единственный ориентир для ревьюера. Worker реализует по спеке, reviewer проверяет по спеке.

### Bootstrapper → Worker

```json
{
  "task_id": "task-123",
  "workspace_id": "ws-abc",
  "capabilities": {
    "runtime": {"language": "go", "version": "1.22"},
    "tools": [...],
    "dependencies": [...],
    "test_command": "go test ./...",
    "build_command": "go build -o /tmp/app ./cmd/..."
  }
}
```

### Worker → Reviewer

```json
{
  "task_id": "task-123",
  "workspace_id": "ws-abc",
  "artifacts": [
    {
      "type": "code_diff",
      "path": "/workspace/project",
      "files_changed": ["handler.go", "handler_test.go"],
      "diff": "..."
    }
  ],
  "test_results": {
    "command": "go test ./...",
    "exit_code": 0,
    "stdout": "...",
    "stderr": ""
  }
}
```

### Reviewer → Worker (итерация)

```json
{
  "task_id": "task-123",
  "workspace_id": "ws-abc",
  "iteration": 2,
  "feedback": {
    "verdict": "FAIL",
    "findings": [
      {
        "severity": "error",
        "file": "handler.go",
        "line": 42,
        "message": "Гонка данных: обращение к разделяемой переменной без мьютекса",
        "evidence": "go test -race: WARNING: DATA RACE at handler.go:42"
      }
    ],
    "summary": "Исправить гонку данных в обработчике"
  }
}
```

---

## 6. Состояние сессии (AG-UI)

### STATE_SNAPSHOT (при переподключении)

```json
{
  "type": "STATE_SNAPSHOT",
  "data": {
    "task_id": "task-123",
    "status": "running",
    "iteration": 1,
    "budget": {
      "spent_tokens": 12500,
      "max_tokens": 500000,
      "spent_cost": 0.03
    },
    "workspace_id": "ws-abc"
  }
}
```

### STATE_DELTA (при изменении)

```json
{
  "type": "STATE_DELTA",
  "data": {
    "path": "budget.spent_tokens",
    "value": 15200
  }
}
```

---

## 7. Схема PostgreSQL

```sql
-- Задачи
CREATE TABLE tasks (
    id              TEXT PRIMARY KEY,
    project_id      TEXT,
    input           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'created',
    workspace_id    TEXT REFERENCES workspaces(id),
    iteration       INT NOT NULL DEFAULT 0,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

-- Спецификация задачи (от planner, одобряется пользователем)
CREATE TABLE task_specs (
    id              SERIAL PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id),
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    acceptance_criteria TEXT[] NOT NULL,
    test_plan       JSONB NOT NULL,
    files_affected  TEXT[],
    domains         TEXT[],
    status          TEXT NOT NULL DEFAULT 'pending',
    version         INT NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Бюджет задачи
CREATE TABLE task_budgets (
    task_id         TEXT PRIMARY KEY REFERENCES tasks(id),
    max_tokens      INT NOT NULL DEFAULT 500000,
    max_calls       INT NOT NULL DEFAULT 50,
    max_iterations  INT NOT NULL DEFAULT 3,
    max_duration_s  INT NOT NULL DEFAULT 1800,
    spent_tokens    INT NOT NULL DEFAULT 0,
    spent_calls     INT NOT NULL DEFAULT 0,
    spent_cost      NUMERIC(10,4) NOT NULL DEFAULT 0.0,
    started_at      TIMESTAMPTZ
);

-- Артефакты (код, тесты, диффы от worker)
CREATE TABLE task_artifacts (
    id              SERIAL PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id),
    iteration       INT NOT NULL,
    artifact_type   TEXT NOT NULL,
    path            TEXT,
    files_changed   TEXT[],
    content         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Отчёты ревью
CREATE TABLE task_reviews (
    id              SERIAL PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id),
    iteration       INT NOT NULL,
    verdict         TEXT NOT NULL,
    findings        JSONB,
    summary         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- История решений оркестратора
CREATE TABLE supervisor_decisions (
    id              SERIAL PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id),
    action          TEXT NOT NULL,
    agent_id        TEXT,
    reasoning       TEXT,
    result_status   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- События AG-UI (для переподключения)
CREATE TABLE task_events (
    id              BIGSERIAL PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id),
    seq             INT NOT NULL,
    event_type      TEXT NOT NULL,
    event_data      JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Рабочие окружения
CREATE TABLE workspaces (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    base_image      TEXT NOT NULL,
    dockerfile      TEXT,
    volume_name     TEXT NOT NULL,
    capabilities    JSONB DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'building',
    build_log       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ===== Долговременная память =====

-- Правила (принудительно попадают в промпт)
CREATE TABLE memory_rules (
    id              SERIAL PRIMARY KEY,
    scope_type      TEXT NOT NULL,       -- 'global' | 'workspace' | 'task'
    scope_id        TEXT,                -- workspace_id или task_id; NULL для global
    content         TEXT NOT NULL,        -- текст правила (< 200 токенов рекомендация)
    author_agent    TEXT,                -- кто создал: 'worker', 'bootstrapper', 'user', ...
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Факты (индексируемые, семантически искомые)
CREATE TABLE memory_facts (
    id              SERIAL PRIMARY KEY,
    scope_type      TEXT NOT NULL,       -- 'global' | 'workspace' | 'task'
    scope_id        TEXT,                -- workspace_id или task_id; NULL для global
    content         TEXT NOT NULL,        -- текст факта
    tags            TEXT[],              -- метки для фильтрации
    embedding       vector(384),         -- векторное представление для семантического поиска
    author_agent    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Индексы
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_project ON tasks(project_id);
CREATE INDEX idx_task_specs_task ON task_specs(task_id);
CREATE INDEX idx_task_artifacts_task ON task_artifacts(task_id);
CREATE INDEX idx_task_reviews_task ON task_reviews(task_id);
CREATE INDEX idx_task_events_task_seq ON task_events(task_id, seq);
CREATE INDEX idx_supervisor_decisions_task ON supervisor_decisions(task_id);
CREATE INDEX idx_workspaces_project_status ON workspaces(project_id, status);

-- Индексы памяти
CREATE INDEX idx_memory_rules_scope ON memory_rules(scope_type, scope_id) WHERE active = TRUE;
CREATE INDEX idx_memory_facts_scope ON memory_facts(scope_type, scope_id);
CREATE INDEX idx_memory_facts_embedding ON memory_facts USING ivfflat (embedding vector_cosine_ops);
```

Для векторного поиска используется расширение `pgvector`. Эмбеддинги вычисляются локально (модель `all-MiniLM-L6-v2`, 384 измерения, только CPU).

---

## 8. Ограничения

| Параметр | Значение | Обоснование |
|---|---|---|
| Макс. событий AG-UI на задачу | 10 000 | Предел для переподключения |
| Макс. размер одного правила | ~200 токенов | Правила не должны раздувать промпт |
| Макс. правил на область | 50 | Суммарно ~10K токенов на правила |
| Макс. фактов на область | 10 000 | Разумный предел для поиска |
| Глубина истории ревью | 3 итерации | Совпадает с лимитом итераций |
