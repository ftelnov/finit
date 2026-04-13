# Результаты оценки (Eval Results)

> **Дата**: 2026-04-13  
> **Модель**: MiniMax M2.7 (`/opt/MiniMaxAI/MiniMax-M2.7`) на sglang  
> **Эндпоинт**: 10.70.2.11:8006  

## Сводка

| Уровень | Тесты | Результат | Описание |
|---------|-------|-----------|----------|
| L0 Smoke | 4 | **4/4** | Доступность LLM, детекция языков |
| L4 Pipeline | 2 | **2/2** | Полный пайплайн plan→boot→work→review + LLM Judge |
| L5 Env Challenge | 4 | **4/4** | pyproject.toml, Django, missing deps, TypeScript |
| L7 Rule Compliance | 3 | **3/3** | Запретные файлы, print→logging, docstrings |
| L8 Complex Env | 3 | **3/3** | Рефакторинг, монорепо, нестандартный тест-раннер |
| L6 Continuous | 2 | **2/2** | Многозадачность: Python (health→metrics), Go (healthz→readyz) |
| L9 MCP Discovery | 1 | **1/1** | Обнаружение и использование OpenSearch (opensearch-py) |
| **Итого** | **19** | **19/19** | **100% pass rate, ~16 мин** |

## Детали по уровням

### L0: Smoke-тесты LLM (4/4)

Прямые вызовы к LLM без агентов:
- `/v1/models` доступен, модель `/opt/MiniMaxAI/MiniMax-M2.7` присутствует
- Корректная детекция Python/Flask, Go/Chi, Node/Express по контексту проекта

**Время**: 7 секунд.

### L4: Полный пайплайн с LLM-as-a-Judge (2/2)

| Кейс | Статика | LLM Judge | Итого |
|------|---------|-----------|-------|
| L4-py-flask-full | 9/9 | 25/25 (100%) | 100% |
| L4-go-chi-full | 9/9 | 25/25 (100%) | 100% |

### L5: Нестандартные окружения (4/4)

| Кейс | Что проверяется | Результат |
|------|----------------|-----------|
| L5-py-pyproject-only | Flask определяется из pyproject.toml (нет requirements.txt) | 94% |
| L5-py-django | Django проект (manage.py, settings.py) | PASS |
| L5-go-missing-deps | Go с отсутствующей зависимостью в go.mod | PASS |
| L5-node-typescript | TypeScript Express (tsc, ts-jest) | 85% |

### L7: Соблюдение правил (3/3, все 100%)

| Кейс | Правило | Статика | LLM Judge |
|------|---------|---------|-----------|
| L7-py-no-manifest | Никогда не трогать manifest.json | 9/9 | 25/25 |
| L7-py-no-print | print() запрещён, использовать logging | 9/9 | 25/25 |
| L7-py-docstrings | Все функции — с docstrings | 8/8 | 25/25 |

### L8: Сложные окружения (3/3)

| Кейс | Сложность | Файлов | Команд | Вердикт |
|------|-----------|--------|--------|---------|
| L8-py-refactor-auth | Модификация существующего middleware (UUID валидация) | 2 | 1 | PASS, 0 findings |
| L8-py-monorepo-health | Монорепо: shared/health.py + 2 сервиса | 7 | 3 | PASS, 0 findings |
| L8-node-tap-status | Express с test runner tap (не jest) | 2 | 11 | PASS, 2 info findings |

### L6: Непрерывное расширение (2/2)

Двухшаговые задачи в одном workspace:

**Python Flask**:
- Step 1: `/health` → 100% (статика 7/7, judge 100%)
- Step 2: `/metrics` без поломки `/health` → PASS, worker сохранил /health в коде

**Go Chi**:
- Step 1: `/healthz` → 98% (judge 95%)
- Step 2: `/readyz` без поломки `/healthz` → 94% (judge 100%)

### L9: MCP Discovery — OpenSearch (1/1, 91%)

Bootstrapper обнаружил потребность в OpenSearch, установил `opensearch-py`. Worker написал:
- Скрипт анализа логов (подключение к localhost:9200, запрос по индексу `app-logs-2026.04`)
- 29 тестов покрывающих все acceptance criteria
- JSON-отчёт с top 3 ошибками, worst service, total errors

LLM Judge: correctness 5/5, code quality 4/5, test quality 5/5, environment fit 5/5.

## Архитектура оценки

### Двухуровневая верификация

1. **Статические проверки** (детерминистические):
   - `artifacts_exist`: worker создал хотя бы один файл
   - `tests_ran` / `tests_passed`: тесты запущены и прошли
   - `language_correct`: файлы соответствуют языку
   - `required_patterns` / `forbidden_patterns`: наличие/отсутствие паттернов в коде
   - `forbidden_files_untouched`: запрещённые файлы не были прочитаны
   - `env_language` / `env_framework`: bootstrapper определил язык/фреймворк

2. **LLM-as-a-Judge** (5 измерений × 0-5 баллов):
   - **correctness**: соответствие спецификации
   - **code_quality**: чистота, идиоматичность кода
   - **test_quality**: покрытие тестами
   - **environment_fit**: соответствие языку/фреймворку проекта
   - **rule_compliance**: соблюдение правил (если заданы)

Общий балл: 50% статика + 50% LLM judge. Порог прохождения: ≥80% статика, ≥50% общий.

### Набор данных: 28 eval-кейсов по 9 уровням

```
L1 (детекция):        6 кейсов — Flask, FastAPI, Go Chi, Go stdlib, Express, Rust Actix
L2 (env-aware):       3 кейса — генерация кода на правильном языке
L3 (persistence):     2 кейса — персистентность workspace
L4 (pipeline):        2 кейса — полный пайплайн с LLM judge
L5 (env challenge):   4 кейса — pyproject.toml, Django, missing deps, TypeScript
L6 (continuous):      4 кейса — многозадачное расширение в одном workspace
L7 (rules):           3 кейса — запретные файлы, print(), docstrings
L8 (complex):         3 кейса — рефакторинг, монорепо, нестандартный тест-раннер
L9 (MCP discovery):   1 кейс  — обнаружение и использование OpenSearch
```

### Фикстуры проектов (`evals/fixtures/`)

| Модуль | Проекты |
|--------|---------|
| `python.py` | Flask, FastAPI, Django, Flask+pyproject, Flask+manifest, Flask+auth middleware, монорепо |
| `go.py` | Chi, stdlib, missing deps |
| `node.py` | Express (JS), TypeScript Express, tap test runner |
| `rust.py` | Actix-web |

## Supervisor Agent

Supervisor — LLM-driven агент с 13 платформенными инструментами. Проверен на реальной задаче:

```
turn 0:  get_task                       → прочитал задачу
turn 1:  dispatch_agent(planner)        → вызвал планировщик
turn 2:  save_spec                      → сохранил спецификацию
turn 3:  request_user_approval          → одобрение пользователя
turn 4:  get_budget                     → проверил бюджет
turn 5:  dispatch_agent(bootstrapper)   → определил окружение
turn 6:  dispatch_agent(worker)         → первая реализация (3 файла, 8 команд)
turn 7:  store_artifacts                → сохранил артефакты
turn 8:  dispatch_agent(reviewer)       → FAIL, 3 замечания
turn 9:  dispatch_agent(worker)         → повторная реализация с фидбеком
turn 10-12: ещё 2 итерации work→review
turn 13: store_review                   → PASS, 2 info-замечания
turn 14: complete_task                  → задача завершена успешно
```

Время: ~2.5 минуты. Все решения принимает LLM через tool calls.

## Запуск

```bash
# 1. Поднять стек
docker compose -f docker-compose.eval.yml up -d

# 2. Зарегистрировать агентов
for p in 9000 9001 9002 9003; do
  curl -s -X POST http://localhost:8080/api/agents \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"http://localhost:$p\"}"
done

# 3. (Опционально) Поднять OpenSearch для L9
docker compose -f docker-compose.eval.yml up -d opensearch
cd evals && python seed_opensearch.py

# 4. Запустить тесты
cd evals
JWT_SECRET=dev-secret pytest test_smoke_llm.py test_rule_compliance.py \
  test_judged_pipeline.py test_env_challenge.py test_complex_env.py \
  test_continuous.py test_mcp_discovery.py -v --timeout=300
```
