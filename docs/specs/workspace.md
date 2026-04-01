# Спецификация: Рабочее окружение и Bootstrapper

## Назначение

Рабочее окружение (workspace) -- единица изоляции и воспроизводимости. Bootstrapper -- A2A-агент, отвечающий за создание, обновление и поддержку окружений.

## Модель окружения

### Структура

```
Workspace:
  id:                 string    # "ws-{sha256(project+config)[:12]}"
  project_id:         string    # привязка к проекту
  base_image:         string    # "finit/workspace-go:1.22"
  dockerfile_content: string    # сгенерированный Dockerfile
  volume_name:        string    # Docker volume
  status:             enum      # building | ready | failed | archived
  capabilities:       WorkspaceCapabilities
  created_at:         timestamp
  last_used_at:       timestamp
  build_log:          string    # журнал сборки
```

### Возможности окружения (WorkspaceCapabilities)

```json
{
  "runtime": {
    "language": "go",
    "version": "1.22",
    "framework": "chi"
  },
  "tools": [
    {"name": "go", "version": "1.22.4", "path": "/usr/local/go/bin/go"},
    {"name": "golangci-lint", "version": "1.59.0", "path": "/usr/local/bin/golangci-lint"},
    {"name": "delve", "version": "1.23.0", "path": "/usr/local/bin/dlv"}
  ],
  "dependencies": [
    {"name": "github.com/go-chi/chi/v5", "version": "v5.0.12"},
    {"name": "github.com/prometheus/client_golang", "version": "v1.19.0"}
  ],
  "test_command": "go test ./...",
  "lint_command": "golangci-lint run",
  "build_command": "go build -o /tmp/app ./cmd/..."
}
```

## Bootstrapper

### Карточка агента (A2A Agent Card)

```json
{
  "name": "bootstrapper",
  "description": "Управляет рабочими окружениями: создание, обновление, установка зависимостей и инструментов",
  "url": "http://bootstrapper:9001",
  "version": "1.0.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false
  },
  "skills": [
    {
      "id": "prepare_workspace",
      "name": "Подготовить окружение",
      "description": "Проанализировать задачу, создать или переиспользовать окружение, установить зависимости"
    },
    {
      "id": "extend_workspace",
      "name": "Расширить окружение",
      "description": "Добавить инструменты, зависимости или MCP-серверы в существующее окружение"
    }
  ]
}
```

### Процесс подготовки окружения

```
1. Получить спеку + метаданные проекта
2. LLM-вызов: анализ требований
   → определить: язык, версия среды выполнения, фреймворки, инструменты
3. Проверить: существует ли подходящее окружение?
   → Да: переиспользовать (обновить last_used_at, проверить возможности)
   → Нет: создать новое
4. Генерация Dockerfile:
   - FROM finit/workspace-{language}:{version}
   - COPY файлы проекта
   - RUN установка зависимостей
   - RUN установка дополнительных инструментов
5. Сборка Docker-образа → Docker volume
6. Проверка: запуск tool --version для каждого инструмента
7. Запуск MCP-серверов внутри окружения
8. Монтирование секретов (переменные среды / файлы)
9. Вернуть workspace_id + возможности + адреса MCP-серверов
```

### Базовые образы

Предсобранные образы с языковыми инструментами:

| Образ | Содержит |
|---|---|
| `finit/workspace-go:1.22` | Go 1.22, git, make, golangci-lint |
| `finit/workspace-python:3.12` | Python 3.12, pip, venv, ruff, mypy |
| `finit/workspace-node:20` | Node.js 20, npm, eslint, prettier |
| `finit/workspace-base` | git, curl, jq, yq -- минимальный |

### Генерация Dockerfile

Bootstrapper генерирует Dockerfile через LLM-вызов со структурированным выводом:

```json
{
  "base_image": "finit/workspace-go:1.22",
  "system_packages": ["protobuf-compiler"],
  "tool_installs": [
    {"name": "buf", "install": "go install github.com/bufbuild/buf/cmd/buf@latest"}
  ],
  "dependency_install": "go mod download",
  "verification": [
    "go version",
    "buf --version",
    "golangci-lint --version"
  ]
}
```

### Управление томами Docker

```
/var/lib/docker/volumes/
  └── finit-ws-{workspace_id}/
       └── _data/
            ├── project/          # исходный код
            ├── tools/            # дополнительные инструменты
            └── .finit/
                 ├── capabilities.json
                 ├── Dockerfile
                 └── build.log
```

### Взаимодействие с worker и reviewer

Worker и reviewer -- LLM-агенты, вызывающие MCP-инструменты внутри уже подготовленного окружения. Они не управляют контейнерами. Bootstrapper отвечает за жизненный цикл окружения.

```
Bootstrapper:
  1. Создаёт контейнер окружения (Docker / Firecracker)
  2. Устанавливает зависимости, инструменты
  3. Запускает MCP-серверы внутри окружения
  4. Монтирует секреты (переменные среды / файлы)
  5. Возвращает workspace_id + адреса MCP-серверов

Worker / Reviewer:
  - Вызывают MCP-инструменты в окружении (чтение/запись файлов, выполнение команд, запуск тестов, линтинг)
  - НЕ создают контейнеры, НЕ управляют их жизненным циклом
  - Доступ к окружению только через MCP
```

### Различия окружений

| Параметр | Окружение worker | Окружение reviewer |
|---|---|---|
| MCP-инструменты | file_read, file_write, bash_exec, test_run | file_read, bash_exec (только чтение), test_run, lint |
| Доступ к файлам | чтение-запись | только чтение |
| Ресурсы | CPU 2 ядра, RAM 4 ГБ, PIDs 256 | CPU 1 ядро, RAM 2 ГБ, PIDs 128 |
| Секреты | Примонтированы (переменные среды / файлы) | Не примонтированы |

## Жизненный цикл окружения

```
[создание] → building → ready → [используется worker/reviewer] → ready → ... → archived
                  ↓
               failed → [повтор с запасным образом] → building
```

- **building**: идёт сборка Docker-образа
- **ready**: окружение готово, инструменты проверены
- **failed**: сборка не удалась, журнал содержит ошибку
- **archived**: окружение не использовалось > 7 дней, том удалён

### Переиспользование

Окружение переиспользуется между задачами одного проекта если:
1. Базовый образ совпадает
2. Требуемые возможности -- подмножество установленных
3. Статус = `ready`

При этом `last_used_at` обновляется, окружение не пересобирается.

### Очистка

Ежедневная задача:
- Окружения с `last_used_at > 7 дней` → архивация (удалить том)
- Журналы сборки старше 30 дней → удаление

## Ограничения

| Параметр | Значение |
|---|---|
| Макс. время сборки окружения | 120 с |
| Макс. размер тома | 10 ГБ |
| Макс. одновременных сборок | 2 |
| Таймаут загрузки базового образа | 60 с |
| Таймаут проверки инструмента | 10 с |

## Ошибки

| Ошибка | Причина | Восстановление |
|---|---|---|
| Ошибка сборки Docker | Некорректный Dockerfile, отсутствующий пакет | Повтор с упрощённым Dockerfile |
| Том заполнен | Слишком много зависимостей | Увеличить лимит или очистка |
| Проверка инструмента не прошла | Инструмент установлен некорректно | Повтор установки альтернативным способом |
| Базовый образ не найден | Реестр образов недоступен | Использовать кэшированный образ |
| Docker-сокет недоступен | Docker не запущен | Немедленная ошибка, уведомление пользователя |
