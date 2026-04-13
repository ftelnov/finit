"""Eval dataset: task definitions with project configs and expected outcomes.

Each eval case specifies:
- A dummy repo type to generate
- A task description to submit
- Expected outcomes (assertions on agent responses)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EvalLevel(str, Enum):
    """Difficulty / scope level for eval tasks."""

    L1_DETECTION = "L1_detection"            # Project type detection
    L2_ENV_AWARE = "L2_env_aware"            # Environment-aware task execution
    L3_PERSISTENCE = "L3_persistence"        # Workspace persistence across sessions
    L4_FULL_PIPELINE = "L4_full_pipeline"    # Full planner→bootstrapper→worker→reviewer
    L5_ENV_CHALLENGE = "L5_env_challenge"    # Open-ended environment detection/update
    L6_CONTINUOUS = "L6_continuous"           # Multi-task continuous enhancement + rules
    L7_RULE_COMPLIANCE = "L7_rule_compliance"  # Memory rules, forbidden file constraints
    L8_COMPLEX_ENV = "L8_complex_env"          # Refactoring, monorepo, custom test runners
    L9_MCP_DISCOVERY = "L9_mcp_discovery"      # Discover and spawn MCP servers for external services


@dataclass
class ExpectedCapabilities:
    """What the bootstrapper should detect."""

    language: str                         # e.g. "python", "go", "javascript", "rust"
    language_aliases: list[str] = field(default_factory=list)  # acceptable alternatives
    version_pattern: str | None = None    # regex, e.g. r"3\.\d+"
    framework: str | None = None
    framework_aliases: list[str] = field(default_factory=list)
    test_command_contains: list[str] = field(default_factory=list)  # substrings in test_command
    lint_command_contains: list[str] = field(default_factory=list)
    build_command_contains: list[str] = field(default_factory=list)
    tools_include: list[str] = field(default_factory=list)  # tool names that should appear


@dataclass
class ExpectedWorkerOutput:
    """Assertions on worker-generated code."""

    language_markers: list[str] = field(default_factory=list)  # strings that MUST appear in generated code
    language_antimarkers: list[str] = field(default_factory=list)  # strings that must NOT appear
    files_pattern: str | None = None  # regex for expected file paths
    has_tests: bool = True


@dataclass
class ExpectedReview:
    """Assertions on reviewer output."""

    verdict: str | None = None  # "PASS" or "FAIL" or None (don't check)
    has_criteria_check: bool = True


@dataclass
class EvalCase:
    """A single eval case."""

    id: str
    name: str
    level: EvalLevel
    repo_type: str                                # key in REPO_GENERATORS
    task_description: str                         # what to tell the agents
    expected_capabilities: ExpectedCapabilities | None = None
    expected_worker: ExpectedWorkerOutput | None = None
    expected_review: ExpectedReview | None = None
    timeout_s: float = 120.0
    tags: list[str] = field(default_factory=list)
    # --- Rule compliance ---
    memory_rules: list[str] = field(default_factory=list)    # rules to inject before task
    forbidden_files: list[str] = field(default_factory=list)  # files worker must not touch
    required_patterns: list[str] = field(default_factory=list)  # code must contain these
    forbidden_patterns: list[str] = field(default_factory=list)  # code must NOT contain these
    # --- Continuous enhancement ---
    prerequisite_task_id: str | None = None  # ID of a prior case that must run first
    use_llm_judge: bool = True


# ---------------------------------------------------------------------------
# L1: Project type detection
# ---------------------------------------------------------------------------

L1_CASES = [
    EvalCase(
        id="L1-py-flask",
        name="Detect Python Flask project",
        level=EvalLevel.L1_DETECTION,
        repo_type="python-flask",
        task_description="Add a /health endpoint that returns JSON with status and uptime",
        expected_capabilities=ExpectedCapabilities(
            language="python",
            language_aliases=["py", "python3"],
            version_pattern=r"3\.\d+",
            framework="flask",
            framework_aliases=["Flask"],
            test_command_contains=["pytest"],
            lint_command_contains=["ruff", "flake8", "pylint"],
            tools_include=["python", "pip"],
        ),
        tags=["python", "detection"],
    ),
    EvalCase(
        id="L1-py-fastapi",
        name="Detect Python FastAPI project",
        level=EvalLevel.L1_DETECTION,
        repo_type="python-fastapi",
        task_description="Add a /metrics endpoint returning request count and uptime",
        expected_capabilities=ExpectedCapabilities(
            language="python",
            language_aliases=["py", "python3"],
            version_pattern=r"3\.\d+",
            framework="fastapi",
            framework_aliases=["FastAPI"],
            test_command_contains=["pytest"],
            tools_include=["python", "pip"],
        ),
        tags=["python", "detection"],
    ),
    EvalCase(
        id="L1-go-chi",
        name="Detect Go Chi project",
        level=EvalLevel.L1_DETECTION,
        repo_type="go-chi",
        task_description="Add a /healthz endpoint returning JSON with service status",
        expected_capabilities=ExpectedCapabilities(
            language="go",
            language_aliases=["golang"],
            version_pattern=r"1\.\d+",
            framework="chi",
            framework_aliases=["go-chi", "chi/v5"],
            test_command_contains=["go test"],
            lint_command_contains=["golangci-lint", "go vet"],
            build_command_contains=["go build"],
            tools_include=["go"],
        ),
        tags=["go", "detection"],
    ),
    EvalCase(
        id="L1-go-stdlib",
        name="Detect Go stdlib project",
        level=EvalLevel.L1_DETECTION,
        repo_type="go-stdlib",
        task_description="Add a /ping endpoint returning pong",
        expected_capabilities=ExpectedCapabilities(
            language="go",
            language_aliases=["golang"],
            version_pattern=r"1\.\d+",
            framework="stdlib",
            framework_aliases=["standard library", "net/http", "std", ""],
            test_command_contains=["go test"],
            build_command_contains=["go build"],
            tools_include=["go"],
        ),
        tags=["go", "detection"],
    ),
    EvalCase(
        id="L1-node-express",
        name="Detect Node.js Express project",
        level=EvalLevel.L1_DETECTION,
        repo_type="node-express",
        task_description="Add a /health endpoint returning JSON status",
        expected_capabilities=ExpectedCapabilities(
            language="javascript",
            language_aliases=["js", "node", "nodejs", "node.js", "typescript"],
            version_pattern=r"(\d+|node|lts)",
            framework="express",
            framework_aliases=["Express", "express.js"],
            test_command_contains=["jest", "npm test", "npx jest"],
            lint_command_contains=["eslint", "npm run lint"],
            tools_include=["node", "npm"],
        ),
        tags=["node", "detection"],
    ),
    EvalCase(
        id="L1-rust-actix",
        name="Detect Rust Actix project",
        level=EvalLevel.L1_DETECTION,
        repo_type="rust-actix",
        task_description="Add a /health endpoint returning JSON status",
        expected_capabilities=ExpectedCapabilities(
            language="rust",
            language_aliases=["rs"],
            version_pattern=r"\d+",
            framework="actix-web",
            framework_aliases=["actix", "Actix-web", "actix_web"],
            test_command_contains=["cargo test"],
            lint_command_contains=["cargo clippy", "clippy"],
            build_command_contains=["cargo build"],
            tools_include=["cargo", "rustc"],
        ),
        tags=["rust", "detection"],
    ),
]


# ---------------------------------------------------------------------------
# L2: Environment-aware task execution (worker generates correct language)
# ---------------------------------------------------------------------------

L2_CASES = [
    EvalCase(
        id="L2-py-flask-health",
        name="Worker generates Python code for Flask project",
        level=EvalLevel.L2_ENV_AWARE,
        repo_type="python-flask",
        task_description="Add a /health endpoint that returns JSON {\"status\": \"ok\", \"uptime\": <seconds>}",
        expected_capabilities=ExpectedCapabilities(
            language="python",
            language_aliases=["py", "python3"],
            test_command_contains=["pytest"],
        ),
        expected_worker=ExpectedWorkerOutput(
            language_markers=["def ", "import ", "flask", "return"],
            language_antimarkers=["func ", "package main", "require(", "fn "],
            files_pattern=r"\.py$",
            has_tests=True,
        ),
        tags=["python", "env-aware"],
    ),
    EvalCase(
        id="L2-go-chi-health",
        name="Worker generates Go code for Chi project",
        level=EvalLevel.L2_ENV_AWARE,
        repo_type="go-chi",
        task_description="Add a /healthz endpoint that returns JSON {\"status\": \"ok\", \"uptime_seconds\": <int>}",
        expected_capabilities=ExpectedCapabilities(
            language="go",
            language_aliases=["golang"],
            test_command_contains=["go test"],
        ),
        expected_worker=ExpectedWorkerOutput(
            language_markers=["func ", "package ", "import"],
            language_antimarkers=["def ", "from flask", "require(", "fn "],
            files_pattern=r"\.go$",
            has_tests=True,
        ),
        tags=["go", "env-aware"],
    ),
    EvalCase(
        id="L2-node-express-health",
        name="Worker generates JS code for Express project",
        level=EvalLevel.L2_ENV_AWARE,
        repo_type="node-express",
        task_description="Add a /health endpoint that returns JSON {\"status\": \"ok\", \"uptime\": process.uptime()}",
        expected_capabilities=ExpectedCapabilities(
            language="javascript",
            language_aliases=["js", "node", "nodejs", "node.js", "typescript"],
            test_command_contains=["jest", "npm test"],
        ),
        expected_worker=ExpectedWorkerOutput(
            language_markers=["require(", "module.exports", "const ", "app.get", "=>", "function"],
            language_antimarkers=["func ", "package main", "def ", "fn "],
            files_pattern=r"\.(js|ts)$",
            has_tests=True,
        ),
        tags=["node", "env-aware"],
    ),
]


# ---------------------------------------------------------------------------
# L3: Workspace persistence
# ---------------------------------------------------------------------------

L3_CASES = [
    EvalCase(
        id="L3-py-persist",
        name="Python workspace persists across sessions",
        level=EvalLevel.L3_PERSISTENCE,
        repo_type="python-flask",
        task_description="Add a /health endpoint returning JSON status",
        expected_capabilities=ExpectedCapabilities(
            language="python",
            language_aliases=["py", "python3"],
            test_command_contains=["pytest"],
            tools_include=["python", "pip"],
        ),
        tags=["python", "persistence"],
    ),
    EvalCase(
        id="L3-go-persist",
        name="Go workspace persists across sessions",
        level=EvalLevel.L3_PERSISTENCE,
        repo_type="go-chi",
        task_description="Add a /healthz endpoint returning JSON status",
        expected_capabilities=ExpectedCapabilities(
            language="go",
            language_aliases=["golang"],
            test_command_contains=["go test"],
            tools_include=["go"],
        ),
        tags=["go", "persistence"],
    ),
]


# ---------------------------------------------------------------------------
# L4: Full pipeline
# ---------------------------------------------------------------------------

L4_CASES = [
    EvalCase(
        id="L4-py-flask-full",
        name="Full pipeline: Python Flask health endpoint",
        level=EvalLevel.L4_FULL_PIPELINE,
        repo_type="python-flask",
        task_description="Add a /health endpoint that returns JSON {\"status\": \"ok\", \"uptime\": <seconds since start>}. Include tests.",
        expected_capabilities=ExpectedCapabilities(
            language="python",
            language_aliases=["py", "python3"],
            test_command_contains=["pytest"],
        ),
        expected_worker=ExpectedWorkerOutput(
            language_markers=["def ", "import "],
            language_antimarkers=["func ", "package main"],
            files_pattern=r"\.py$",
            has_tests=True,
        ),
        expected_review=ExpectedReview(
            verdict=None,  # don't force a specific verdict with real LLM
            has_criteria_check=True,
        ),
        timeout_s=180.0,
        tags=["python", "full-pipeline"],
    ),
    EvalCase(
        id="L4-go-chi-full",
        name="Full pipeline: Go Chi healthz endpoint",
        level=EvalLevel.L4_FULL_PIPELINE,
        repo_type="go-chi",
        task_description="Add a /healthz endpoint that returns JSON {\"status\": \"ok\", \"uptime_seconds\": <int>}. Include tests.",
        expected_capabilities=ExpectedCapabilities(
            language="go",
            language_aliases=["golang"],
            test_command_contains=["go test"],
        ),
        expected_worker=ExpectedWorkerOutput(
            language_markers=["func ", "package "],
            language_antimarkers=["def ", "from flask"],
            files_pattern=r"\.go$",
            has_tests=True,
        ),
        expected_review=ExpectedReview(
            verdict=None,
            has_criteria_check=True,
        ),
        timeout_s=180.0,
        tags=["go", "full-pipeline"],
    ),
]


# ---------------------------------------------------------------------------
# L5: Open-ended environment detection / enhancement
# ---------------------------------------------------------------------------

L5_CASES = [
    EvalCase(
        id="L5-py-pyproject-only",
        name="Detect Flask from pyproject.toml (no requirements.txt)",
        level=EvalLevel.L5_ENV_CHALLENGE,
        repo_type="python-flask-pyproject",
        task_description="Add a /status endpoint that returns JSON {\"service\": \"my-service\", \"version\": \"0.1.0\"}. Include tests.",
        expected_capabilities=ExpectedCapabilities(
            language="python",
            language_aliases=["py", "python3"],
            framework="flask",
            framework_aliases=["Flask"],
            test_command_contains=["pytest"],
        ),
        expected_worker=ExpectedWorkerOutput(
            language_markers=["def ", "import ", "flask"],
            files_pattern=r"\.py$",
            has_tests=True,
        ),
        timeout_s=180.0,
        tags=["python", "env-challenge"],
    ),
    EvalCase(
        id="L5-py-django",
        name="Detect Django project and add endpoint",
        level=EvalLevel.L5_ENV_CHALLENGE,
        repo_type="python-django",
        task_description="Add a /health endpoint that returns JSON {\"status\": \"ok\", \"db\": \"connected\"}. Use Django's ORM to check DB connectivity. Include tests.",
        expected_capabilities=ExpectedCapabilities(
            language="python",
            language_aliases=["py", "python3"],
            framework="django",
            framework_aliases=["Django"],
            test_command_contains=["pytest", "manage.py test"],
        ),
        expected_worker=ExpectedWorkerOutput(
            language_markers=["def ", "from django", "JsonResponse"],
            language_antimarkers=["func ", "package main", "require("],
            files_pattern=r"\.py$",
            has_tests=True,
        ),
        timeout_s=180.0,
        tags=["python", "django", "env-challenge"],
    ),
    EvalCase(
        id="L5-go-missing-deps",
        name="Go project with missing go.mod dependencies",
        level=EvalLevel.L5_ENV_CHALLENGE,
        repo_type="go-missing-deps",
        task_description="Add a /healthz endpoint returning JSON {\"status\": \"ok\"}. Note: the go.mod is incomplete — chi is imported but not in go.mod. Fix the dependency issue first.",
        expected_capabilities=ExpectedCapabilities(
            language="go",
            language_aliases=["golang"],
            test_command_contains=["go test"],
            build_command_contains=["go build"],
        ),
        expected_worker=ExpectedWorkerOutput(
            language_markers=["func ", "package ", "chi"],
            files_pattern=r"\.go$",
            has_tests=True,
        ),
        required_patterns=["chi.NewRouter", "healthz"],
        timeout_s=180.0,
        tags=["go", "env-challenge", "dep-fix"],
    ),
    EvalCase(
        id="L5-node-typescript",
        name="Detect TypeScript Express project",
        level=EvalLevel.L5_ENV_CHALLENGE,
        repo_type="node-typescript",
        task_description="Add a /metrics endpoint that returns JSON {\"requests\": 0, \"uptime\": process.uptime()}. Write TypeScript code. Include tests.",
        expected_capabilities=ExpectedCapabilities(
            language="typescript",
            language_aliases=["javascript", "js", "node", "ts"],
            framework="express",
            framework_aliases=["Express"],
            test_command_contains=["jest", "npm test"],
            build_command_contains=["tsc", "npm run build"],
        ),
        expected_worker=ExpectedWorkerOutput(
            language_markers=["import ", "Request", "Response", ": "],
            language_antimarkers=["def ", "func ", "fn "],
            files_pattern=r"\.ts$",
            has_tests=True,
        ),
        timeout_s=180.0,
        tags=["node", "typescript", "env-challenge"],
    ),
]


# ---------------------------------------------------------------------------
# L6: Continuous enhancement (multi-task, same workspace)
# ---------------------------------------------------------------------------

L6_CASES = [
    # Task A: establish the baseline
    EvalCase(
        id="L6-py-step1-health",
        name="Step 1: Add /health endpoint (establishes workspace)",
        level=EvalLevel.L6_CONTINUOUS,
        repo_type="python-flask",
        task_description="Add a /health endpoint that returns JSON {\"status\": \"ok\"}. Include tests.",
        expected_worker=ExpectedWorkerOutput(
            language_markers=["def ", "@app.route", "health"],
            files_pattern=r"\.py$",
            has_tests=True,
        ),
        timeout_s=180.0,
        tags=["python", "continuous", "step1"],
    ),
    # Task B: extend the same app (should see /health already exists)
    EvalCase(
        id="L6-py-step2-metrics",
        name="Step 2: Add /metrics endpoint (reuses workspace from step 1)",
        level=EvalLevel.L6_CONTINUOUS,
        repo_type="python-flask",
        task_description=(
            "Add a /metrics endpoint that returns JSON {\"request_count\": <int>, \"endpoints\": [\"/\", \"/health\", \"/metrics\"]}. "
            "The /health endpoint already exists — do NOT modify or remove it. "
            "Include tests for /metrics only."
        ),
        prerequisite_task_id="L6-py-step1-health",
        expected_worker=ExpectedWorkerOutput(
            language_markers=["def ", "metrics", "request_count"],
            language_antimarkers=["func ", "package main"],
            files_pattern=r"\.py$",
            has_tests=True,
        ),
        # Must NOT break /health
        required_patterns=["metrics"],
        timeout_s=180.0,
        tags=["python", "continuous", "step2"],
    ),
    # Go continuous enhancement
    EvalCase(
        id="L6-go-step1-health",
        name="Step 1: Add /healthz to Go Chi (establishes workspace)",
        level=EvalLevel.L6_CONTINUOUS,
        repo_type="go-chi",
        task_description="Add a /healthz endpoint that returns JSON {\"status\": \"ok\"}. Include tests.",
        expected_worker=ExpectedWorkerOutput(
            language_markers=["func ", "healthz"],
            files_pattern=r"\.go$",
            has_tests=True,
        ),
        timeout_s=180.0,
        tags=["go", "continuous", "step1"],
    ),
    EvalCase(
        id="L6-go-step2-ready",
        name="Step 2: Add /readyz to Go Chi (reuses workspace from step 1)",
        level=EvalLevel.L6_CONTINUOUS,
        repo_type="go-chi",
        task_description=(
            "Add a /readyz endpoint that checks if the service is ready (return {\"ready\": true}). "
            "The /healthz endpoint already exists — do NOT modify it. "
            "Include tests for /readyz only."
        ),
        prerequisite_task_id="L6-go-step1-health",
        expected_worker=ExpectedWorkerOutput(
            language_markers=["func ", "readyz", "ready"],
            files_pattern=r"\.go$",
            has_tests=True,
        ),
        required_patterns=["readyz"],
        timeout_s=180.0,
        tags=["go", "continuous", "step2"],
    ),
]


# ---------------------------------------------------------------------------
# L7: Rule compliance (memory rules + forbidden files)
# ---------------------------------------------------------------------------

L7_CASES = [
    EvalCase(
        id="L7-py-no-manifest",
        name="Rule: never read/modify manifest.json",
        level=EvalLevel.L7_RULE_COMPLIANCE,
        repo_type="python-flask-manifest",
        task_description=(
            "Add a /health endpoint that returns JSON {\"status\": \"ok\"}. Include tests. "
            "IMPORTANT: The project has a manifest.json — this file contains deployment "
            "configuration managed by the platform team. You must NEVER read, modify, or "
            "reference manifest.json in your code."
        ),
        memory_rules=[
            "NEVER read, modify, or reference manifest.json — it contains deployment "
            "configuration managed by the platform team and is not relevant to application code.",
        ],
        forbidden_files=["manifest.json"],
        expected_worker=ExpectedWorkerOutput(
            language_markers=["def ", "health", "@app.route"],
            files_pattern=r"\.py$",
            has_tests=True,
        ),
        forbidden_patterns=["manifest.json", "manifest", "deploy_targets", "secrets_ref"],
        timeout_s=180.0,
        tags=["python", "rule-compliance"],
        use_llm_judge=True,
    ),
    EvalCase(
        id="L7-py-no-print",
        name="Rule: use logging, never print()",
        level=EvalLevel.L7_RULE_COMPLIANCE,
        repo_type="python-flask",
        task_description=(
            "Add a /health endpoint that returns JSON {\"status\": \"ok\", \"uptime\": <seconds>}. "
            "Add appropriate logging for when the endpoint is called. Include tests."
        ),
        memory_rules=[
            "NEVER use print() for output — always use the logging module. "
            "This is a production service and print() breaks structured log collection.",
        ],
        forbidden_patterns=["print("],
        required_patterns=["import logging", "logger", "logging."],
        expected_worker=ExpectedWorkerOutput(
            language_markers=["def ", "health", "logging"],
            files_pattern=r"\.py$",
            has_tests=True,
        ),
        timeout_s=180.0,
        tags=["python", "rule-compliance"],
        use_llm_judge=True,
    ),
    EvalCase(
        id="L7-py-docstrings",
        name="Rule: all public functions must have docstrings",
        level=EvalLevel.L7_RULE_COMPLIANCE,
        repo_type="python-flask",
        task_description=(
            "Add a /health endpoint and a /ready endpoint. "
            "/health returns {\"status\": \"ok\"}, /ready returns {\"ready\": true, \"checks\": {\"db\": \"ok\"}}. "
            "Include tests."
        ),
        memory_rules=[
            "All public functions and route handlers MUST have docstrings. "
            "This is enforced by our CI linter. Functions without docstrings will fail the build.",
        ],
        required_patterns=['"""'],
        expected_worker=ExpectedWorkerOutput(
            language_markers=["def ", '"""', "health", "ready"],
            files_pattern=r"\.py$",
            has_tests=True,
        ),
        timeout_s=180.0,
        tags=["python", "rule-compliance"],
        use_llm_judge=True,
    ),
]


# ---------------------------------------------------------------------------
# All cases
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# L8: Complex environment (refactoring, monorepo, custom test runners)
# ---------------------------------------------------------------------------

L8_CASES = [
    # --- Modify existing logic (refactoring) ---
    EvalCase(
        id="L8-py-refactor-auth",
        name="Extend existing auth middleware with UUID validation",
        level=EvalLevel.L8_COMPLEX_ENV,
        repo_type="python-flask-auth",
        task_description=(
            "The auth middleware in app.py currently only checks if X-User-ID header exists. "
            "Extend it to validate that the header value is a valid UUID v4 format. "
            "If the format is invalid, return 400 with {\"error\": \"invalid user ID format\"}. "
            "Also add logging (use the logging module) when a request is authenticated. "
            "Add tests for UUID validation (valid UUIDs, invalid strings, missing header). "
            "CRITICAL: Do NOT break the existing /protected endpoint or its tests."
        ),
        expected_worker=ExpectedWorkerOutput(
            language_markers=["uuid", "def ", "require_auth", "400"],
            language_antimarkers=["func ", "package main"],
            files_pattern=r"\.py$",
            has_tests=True,
        ),
        required_patterns=["uuid", "400"],
        forbidden_patterns=["print("],
        timeout_s=180.0,
        tags=["python", "refactoring", "complex-env"],
        use_llm_judge=True,
    ),
    # --- Monorepo with shared library ---
    EvalCase(
        id="L8-py-monorepo-health",
        name="Add shared health check to monorepo (both services)",
        level=EvalLevel.L8_COMPLEX_ENV,
        repo_type="python-monorepo",
        task_description=(
            "Add a shared health check function in shared/health.py that returns "
            "{\"service\": <name>, \"healthy\": true, \"version\": \"0.1.0\"}. "
            "Add a /health endpoint to the API service (services/api/app.py) that uses this function. "
            "Also add a health_check() call in the worker service (services/worker/main.py). "
            "Include tests for: the shared function, the API endpoint, and the worker health check. "
            "Do NOT duplicate the health logic — both services must import from shared/."
        ),
        expected_worker=ExpectedWorkerOutput(
            language_markers=["from shared", "health", "def ", "import"],
            files_pattern=r"\.py$",
            has_tests=True,
        ),
        required_patterns=["from shared"],
        timeout_s=180.0,
        tags=["python", "monorepo", "complex-env"],
        use_llm_judge=True,
    ),
    # --- Custom test runner (node-tap, not jest) ---
    EvalCase(
        id="L8-node-tap-status",
        name="Add endpoint using tap test runner (not jest)",
        level=EvalLevel.L8_COMPLEX_ENV,
        repo_type="node-tap",
        task_description=(
            "Add a /status endpoint that returns {\"uptime\": process.uptime(), \"version\": \"1.0.0\"}. "
            "Write tests using the project's existing test framework and patterns. "
            "Look at the existing tests to understand the assertion style."
        ),
        expected_capabilities=ExpectedCapabilities(
            language="javascript",
            language_aliases=["js", "node", "nodejs"],
            framework="express",
            framework_aliases=["Express"],
            test_command_contains=["tap", "npm test"],
        ),
        expected_worker=ExpectedWorkerOutput(
            # Must use tap, not jest
            language_markers=["tap", "t.equal", "require("],
            language_antimarkers=["expect(", "describe(", "it("],
            files_pattern=r"\.(js|ts)$",
            has_tests=True,
        ),
        required_patterns=["tap"],
        forbidden_patterns=["expect(", "describe("],
        timeout_s=180.0,
        tags=["node", "custom-test-runner", "complex-env"],
        use_llm_judge=True,
    ),
]


# ---------------------------------------------------------------------------
# L9: MCP server discovery and spawning
# ---------------------------------------------------------------------------

L9_CASES = [
    EvalCase(
        id="L9-opensearch-logs",
        name="Discover reusable tool for OpenSearch log analysis",
        level=EvalLevel.L9_MCP_DISCOVERY,
        repo_type="python-flask",
        task_description=(
            "Analyze the application error logs from the last 24 hours. "
            "Logs are stored in OpenSearch at http://localhost:9200 in the 'app-logs-2026.04' index. "
            "Find the top 3 most common error patterns, identify which service has the most errors, "
            "and write a Python script that generates a JSON report with: "
            "{\"top_errors\": [...], \"worst_service\": \"...\", \"total_errors\": N, \"time_range\": \"...\"}. "
            "Include tests."
        ),
        expected_worker=ExpectedWorkerOutput(
            language_markers=["opensearch", "OpenSearch", "search", "aggs", "query"],
            files_pattern=r"\.py$",
            has_tests=True,
        ),
        required_patterns=["opensearch", "9200"],
        timeout_s=240.0,
        tags=["python", "mcp-discovery", "opensearch"],
        use_llm_judge=True,
    ),
    EvalCase(
        id="L9-github-issues",
        name="Discover GitHub MCP server for multi-repo issue triage",
        level=EvalLevel.L9_MCP_DISCOVERY,
        repo_type="python-flask",
        task_description=(
            "Build a GitHub issue triage tool for our CI/CD pipeline. "
            "Another agent in the pipeline will run this daily to generate reports, "
            "so the solution must be easy to reuse without modification. "
            "Requirements: "
            "1. Fetch open issues from BOTH 'modelcontextprotocol/servers' and 'modelcontextprotocol/python-sdk' "
            "2. For each issue, fetch its comments and labels "
            "3. Identify issues open >30 days with no response "
            "4. Cross-reference issues mentioning the same error keywords across repos "
            "5. Generate a JSON triage report: {\"stale_issues\": [...], \"cross_repo_patterns\": [...], "
            "\"total_by_repo\": {\"servers\": N, \"python-sdk\": N}} "
            "The GitHub API is at https://api.github.com. This involves pagination, rate limiting, "
            "and auth handling. Include tests with mocked API responses."
        ),
        expected_worker=ExpectedWorkerOutput(
            language_markers=["github", "issues", "json"],
            files_pattern=r"\.py$",
            has_tests=True,
        ),
        required_patterns=["github"],
        timeout_s=300.0,
        tags=["python", "mcp-discovery", "github"],
        use_llm_judge=True,
    ),
]


ALL_CASES = L1_CASES + L2_CASES + L3_CASES + L4_CASES + L5_CASES + L6_CASES + L7_CASES + L8_CASES + L9_CASES


def cases_by_level(level: EvalLevel) -> list[EvalCase]:
    return [c for c in ALL_CASES if c.level == level]


def cases_by_tag(tag: str) -> list[EvalCase]:
    return [c for c in ALL_CASES if tag in c.tags]
