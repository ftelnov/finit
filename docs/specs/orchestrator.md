# Спецификация: Оркестратор

## Назначение

Оркестратор -- такой же A2A-агент, как и остальные (planner, bootstrapper, worker, reviewer), но с дополнительными обязанностями: приём задач от пользователя, делегация подзадач другим агентам, управление жизненным циклом задач. Принимает решения о маршрутизации через LLM.

## Стек

- **Язык**: Rust
- **HTTP**: `axum`
- **A2A**: JSON-RPC 2.0 (клиент для вызова агентов, сервер для приёма задач)
- **PostgreSQL**: `sqlx`
- **Телеметрия**: `opentelemetry`

## API

### Задачи

| Endpoint | Метод | Описание |
|---|---|---|
| `POST /api/tasks` | POST | Создать задачу |
| `GET /api/tasks` | GET | Список задач (с фильтрами) |
| `GET /api/tasks/{id}` | GET | Получить задачу |
| `DELETE /api/tasks/{id}` | DELETE | Отменить задачу |
| `POST /api/tasks/{id}/input` | POST | Ответ пользователя (одобрение спеки и т.п.) |
| `GET /ag-ui/tasks/{id}/events` | GET (SSE) | Поток событий AG-UI |

### Реестр агентов

| Endpoint | Метод | Описание |
|---|---|---|
| `POST /api/agents` | POST | Зарегистрировать агента (Agent Card) |
| `GET /api/agents` | GET | Список агентов и их состояние |
| `GET /api/agents/{id}` | GET | Карточка агента |
| `DELETE /api/agents/{id}` | DELETE | Удалить агента |

### Системные

| Endpoint | Метод | Описание |
|---|---|---|
| `GET /health` | GET | Проверка работоспособности (PostgreSQL) |
| `GET /metrics` | GET | Метрики Prometheus |

## Машина состояний задачи

```
created → running ⟷ awaiting_input → running → completed
              ↓            ↓
           failed       cancelled
           escalated
```

Состояние `running` охватывает все внутренние решения (постановка, подготовка окружения, разработка, ревью). Оркестратор динамически выбирает следующего агента через LLM.

### Переходы

| Из | В | Условие |
|---|---|---|
| `created` | `running` | Оркестратор запускает цикл управления |
| `running` | `awaiting_input` | Запрос не разрешён другим агентом, либо одобрение спеки |
| `awaiting_input` | `running` | Пользователь или агент предоставил ответ |
| `awaiting_input` | `cancelled` | Пользователь отклонил или отменил |
| `running` | `completed` | Ревьюер подтвердил, LLM подтверждает завершение |
| `running` | `failed` | Неустранимая ошибка после повторных попыток |
| `running` | `escalated` | Исчерпан бюджет или лимит итераций |
| `running` | `cancelled` | Пользователь отменил |

### Условия остановки

- **Бюджет исчерпан**: потраченные токены >= лимит → `escalated`
- **Лимит итераций**: число проходов цикла >= максимум → `escalated`
- **Лимит времени**: > 30 мин → `failed`
- **Отмена пользователем**: из любого состояния → `cancelled`

## Цикл управления (Supervisor Loop)

```
Supervisor.run(task):
  loop:
    state = текущее_состояние(task)
    action = LLM.decide(системный_промпт, state, карточки_агентов, история)

    match action:
      вызвать_агента(agent_id, payload):
        result = a2a_client.send(agent_id, payload)
        match result.status:
          completed → обновить состояние, продолжить цикл
          failed → LLM.decide: повторить? другой агент? эскалировать?
          input_required → разрешить_запрос(result.request)

      ожидать_пользователя(question):
        отправить AG-UI RUN_AWAITING_INPUT
        ждать ответ
        продолжить цикл

      завершить_задачу(артефакты, ревью):
        отправить AG-UI RUN_FINISHED
        return

    проверить_бюджет() → эскалировать при исчерпании
```

### Разрешение `input_required`

```
разрешить_запрос(request, состояние_задачи):
  решение = LLM.route(request, доступные_агенты, состояние_задачи)

  match решение:
    перенаправить_агенту(agent_id, payload):
      → вызвать агента, вернуть результат запросившему
    эскалировать_пользователю(вопрос):
      → отправить AG-UI RUN_AWAITING_INPUT, ждать ответ
```

### Системный промпт оркестратора (набросок)

LLM оркестратора получает:
- Описание доступных агентов (из карточек агентов)
- Текущее состояние задачи (спека, окружение, артефакты, история ревью)
- Историю вызовов (кто что вернул)
- Остаток бюджета (токены, итерации)

LLM возвращает структурированное решение:
```json
{
  "action": "call_agent" | "await_user" | "complete_task" | "escalate",
  "agent_id": "planner" | "bootstrapper" | "worker" | "reviewer",
  "payload": { ... },
  "reasoning": "..."
}
```

Ключевой инвариант: worker и reviewer получают **спеку**, а не исходное описание задачи. Спека -- единственный источник истины для оценки результата.

### Повторные попытки

| Параметр | Значение |
|---|---|
| Максимум повторов на фазу | 2 |
| Задержка между попытками | 5 с |
| Множитель задержки | 2x |

При неудаче после всех попыток: задача переходит в `failed`.

## A2A-клиент

### Вызов агента

```
A2AClient.send_task(agent, request) → TaskResult

Шаги:
  1. Сформировать JSON-RPC 2.0 запрос (method: "tasks/send")
  2. Установить Authorization: Bearer {agent.jwt}
  3. Установить traceparent (W3C Trace Context)
  4. POST на {agent.url}
  5. Разобрать JSON-RPC ответ → TaskResult
  6. Транслировать события A2A → AG-UI
```

### Потоковая передача (tasks/sendSubscribe)

Для worker используется `tasks/sendSubscribe` -- агент передаёт промежуточные результаты:

```
Агент → SSE → A2A-клиент → шина событий AG-UI → SSE → WebUI
```

Соответствие событий A2A → AG-UI:

| Событие A2A | Событие AG-UI |
|---|---|
| `TaskStatusUpdate(working)` | `STEP_STARTED` |
| `TaskStatusUpdate(completed)` | `STEP_FINISHED` |
| `TaskArtifactUpdate(text/plain)` | `TEXT_MESSAGE_CONTENT` |
| `TaskArtifactUpdate(application/json)` | `STATE_DELTA` |

## Реестр агентов

### Регистрация

```
POST /api/agents
{
  "url": "http://bootstrapper:9001"
}
```

Оркестратор:
1. Загрузить `{url}/.well-known/agent.json`
2. Проверить схему карточки агента
3. Сгенерировать JWT с правами доступа
4. Сохранить в PostgreSQL
5. Запустить периодическую проверку работоспособности

### Проверка работоспособности

Периодический запрос `GET {agent.url}/health`, таймаут 5 с, настраиваемый интервал. 3 последовательных неудачи → статус `unhealthy`.

### Хранение карточек агентов (PostgreSQL)

```sql
CREATE TABLE agents (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    url               TEXT NOT NULL,
    agent_card        JSONB NOT NULL,
    jwt_token         TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'healthy',
    last_health_check TIMESTAMPTZ,
    registered_at     TIMESTAMPTZ DEFAULT NOW()
);
```

Полная схема PostgreSQL (tasks, workspaces, task_budget): см. [memory-context.md](memory-context.md).

## Шина событий AG-UI

### Архитектура

```
Ответ агента → Цикл управления → Шина событий → SSE-подписчики
                                       ↓
                                 PostgreSQL (запись)
```

### Переподключение

События AG-UI записываются в PostgreSQL (таблица `task_events`, только добавление). При переподключении клиент отправляет `Last-Event-ID` (порядковый номер), оркестратор переигрывает события из PostgreSQL.

### Отправка событий

```
EventBus.emit(task_id, event):
  1. Сериализовать событие в JSON
  2. INSERT в task_events (task_id, seq, event_json)
  3. Разослать всем активным SSE-подключениям для этой задачи
```

## Конфигурация

```yaml
orchestrator:
  listen: ":8080"
  jwt_secret: "${JWT_SECRET}"

  database:
    url: "postgres://finit:${PG_PASSWORD}@postgres:5432/finit"
    max_connections: 20

  supervisor:
    max_iterations: 3
    max_task_duration: "30m"
    phase_timeout: "10m"
    retry_max: 2
    retry_delay: "5s"

  budget:
    default_max_tokens: 500000
    default_max_calls: 50

  agents:
    health_check_interval: "10s"
    health_check_timeout: "5s"
    unhealthy_threshold: 3

  ag_ui:
    max_events_per_task: 10000

  telemetry:
    otlp_endpoint: "otel-collector:4317"
```
