-- Enable pgvector extension for semantic search embeddings
CREATE EXTENSION IF NOT EXISTS vector;

-- Рабочие окружения (workspaces first, referenced by tasks)
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

-- Реестр агентов
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

-- ===== Долговременная память =====

-- Правила (принудительно попадают в промпт)
CREATE TABLE memory_rules (
    id              SERIAL PRIMARY KEY,
    scope_type      TEXT NOT NULL,
    scope_id        TEXT,
    content         TEXT NOT NULL,
    author_agent    TEXT,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Факты (индексируемые, семантически искомые)
CREATE TABLE memory_facts (
    id              SERIAL PRIMARY KEY,
    scope_type      TEXT NOT NULL,
    scope_id        TEXT,
    content         TEXT NOT NULL,
    tags            TEXT[],
    embedding       vector(384),
    author_agent    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ===== LLM Usage (from llm-router spec) =====

CREATE TABLE llm_usage (
    id              BIGSERIAL PRIMARY KEY,
    request_id      TEXT NOT NULL,
    task_id         TEXT,
    agent_id        TEXT,
    provider_id     TEXT NOT NULL,
    model           TEXT NOT NULL,
    input_tokens    INT NOT NULL DEFAULT 0,
    output_tokens   INT NOT NULL DEFAULT 0,
    cost_dollars    NUMERIC(10,6) NOT NULL DEFAULT 0.0,
    ttft_ms         INT,
    total_latency_ms INT,
    status          INT NOT NULL,
    prompt_version  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ===== Indexes =====

-- Task indexes
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_project ON tasks(project_id);
CREATE INDEX idx_task_specs_task ON task_specs(task_id);
CREATE INDEX idx_task_artifacts_task ON task_artifacts(task_id);
CREATE INDEX idx_task_reviews_task ON task_reviews(task_id);
CREATE INDEX idx_task_events_task_seq ON task_events(task_id, seq);
CREATE INDEX idx_supervisor_decisions_task ON supervisor_decisions(task_id);

-- Workspace indexes
CREATE INDEX idx_workspaces_project_status ON workspaces(project_id, status);

-- Memory indexes
CREATE INDEX idx_memory_rules_scope ON memory_rules(scope_type, scope_id) WHERE active = TRUE;
CREATE INDEX idx_memory_facts_scope ON memory_facts(scope_type, scope_id);
CREATE INDEX idx_memory_facts_embedding ON memory_facts USING hnsw (embedding vector_cosine_ops);

-- LLM usage indexes
CREATE INDEX idx_llm_usage_task ON llm_usage(task_id);
CREATE INDEX idx_llm_usage_agent ON llm_usage(agent_id);
CREATE INDEX idx_llm_usage_created ON llm_usage(created_at);
CREATE INDEX idx_llm_usage_prompt_version ON llm_usage(agent_id, prompt_version) WHERE prompt_version IS NOT NULL;

-- ===== Semantic cache for LLM responses =====

CREATE TABLE llm_cache (
    id              SERIAL PRIMARY KEY,
    model           TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    messages_hash   TEXT NOT NULL,
    embedding       vector(384),
    response        JSONB NOT NULL,
    tokens_saved    INT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL,
    CONSTRAINT llm_cache_exact_uq UNIQUE (model, agent_id, messages_hash)
);

CREATE INDEX idx_llm_cache_expiry ON llm_cache(expires_at);
CREATE INDEX idx_llm_cache_semantic ON llm_cache USING hnsw (embedding vector_cosine_ops);

-- ===== Prompt versioning =====

CREATE TABLE prompt_configs (
    id              SERIAL PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    version         TEXT NOT NULL,
    weight          INT NOT NULL DEFAULT 100,
    template_path   TEXT NOT NULL,
    parameters      JSONB DEFAULT '{}',
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent_id, version)
);
