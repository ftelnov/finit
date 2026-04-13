"""Pydantic models for structured LLM responses.

Single source of truth for all schema-guided agent outputs.
JSON schemas are auto-generated from these models — never duplicated in prompts.

Usage:
    # Pass as response_format to constrain LLM output
    response_format = PlannerSpec.response_format()

    # Inject schema docs into a system prompt
    prompt += PlannerSpec.schema_section()

    # Validate LLM output
    spec = PlannerSpec.model_validate_json(raw)
"""

import json
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Helpers: schema generation (mirrors osworld-purple pattern)
# ---------------------------------------------------------------------------

def _resolve_refs(obj: object, defs: dict) -> object:
    """Recursively inline all $ref references.

    Preserves sibling metadata (description, default) when resolving $ref.
    """
    if isinstance(obj, dict):
        if "$ref" in obj:
            ref_name = obj["$ref"].rsplit("/", 1)[-1]
            resolved = _resolve_refs(defs.get(ref_name, {}), defs)
            # Merge sibling keys (description, default, etc.) into resolved schema
            if isinstance(resolved, dict):
                extras = {k: v for k, v in obj.items() if k != "$ref"}
                if extras:
                    resolved = {**resolved, **extras}
            return resolved
        return {k: _resolve_refs(v, defs) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_refs(item, defs) for item in obj]
    return obj


def _clean_schema(obj: object) -> None:
    """Remove Pydantic metadata noise to keep prompt tokens down.

    Strips ``title`` fields that Pydantic adds to every schema definition
    (e.g. ``"title": "TestPlan"``). Only removes from schema definition dicts
    (those with ``type``/``anyOf``), not from ``properties`` containers where
    ``title`` might be an actual property name.
    """
    if isinstance(obj, dict):
        if "type" in obj or "anyOf" in obj or "allOf" in obj:
            obj.pop("title", None)
        for v in obj.values():
            _clean_schema(v)
    elif isinstance(obj, list):
        for item in obj:
            _clean_schema(item)


class _SchemaBase(BaseModel):
    """Mixin providing schema generation helpers."""

    @classmethod
    def response_format(cls, name: str | None = None) -> dict:
        """Generate OpenAI-compatible ``response_format`` dict."""
        schema = cls.model_json_schema()
        defs = schema.pop("$defs", {})
        schema = _resolve_refs(schema, defs)
        _clean_schema(schema)
        return {
            "type": "json_schema",
            "json_schema": {
                "name": name or cls.__name__.lower(),
                "schema": schema,
            },
        }

    @classmethod
    def tool_schema(cls, name: str | None = None, description: str | None = None) -> dict:
        """Generate OpenAI-compatible tool function schema.

        Args:
            name: Tool function name (defaults to snake_case of class name).
            description: Override description (defaults to class docstring).

        Returns:
            ``{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}``
        """
        schema = cls.model_json_schema()
        defs = schema.pop("$defs", {})
        schema = _resolve_refs(schema, defs)
        _clean_schema(schema)

        # Use class docstring as description fallback
        tool_desc = description or schema.pop("description", None) or (cls.__doc__ or "").strip()

        # Derive snake_case name from class name if not provided
        if name is None:
            import re
            name = re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).lower()
            # Strip common suffixes
            for suffix in ("_tool", "_params", "_args"):
                if name.endswith(suffix):
                    name = name[: -len(suffix)]

        return {
            "type": "function",
            "function": {
                "name": name,
                "description": tool_desc,
                "parameters": schema,
            },
        }

    @classmethod
    def schema_section(cls) -> str:
        """Generate a prompt section documenting the response schema."""
        schema = cls.model_json_schema()
        defs = schema.pop("$defs", {})
        schema = _resolve_refs(schema, defs)
        _clean_schema(schema)
        return (
            "## Response schema\n\n"
            "```json\n"
            + json.dumps(schema, indent=2)
            + "\n```"
        )


# ---------------------------------------------------------------------------
# Planner output
# ---------------------------------------------------------------------------

class TestPlan(BaseModel):
    unit_tests: list[str] = Field(
        default_factory=list,
        description="Concrete test function names to write (e.g. 'test_health_endpoint_returns_200').",
    )
    commands: list[str] = Field(
        default_factory=list,
        description="Runnable shell commands to verify correctness (e.g. 'curl -s localhost:8080/health | jq .status'). No placeholders.",
    )


class PlannerSpec(_SchemaBase):
    """Task specification produced by the planner agent."""

    title: str = Field(
        description="Concise task title: what is being done, not how. Max 200 characters.",
        max_length=200,
    )
    description: str = Field(
        description="Full scope: what changes, why, boundary conditions, and assumptions made.",
        min_length=1,
    )
    acceptance_criteria: list[str] = Field(
        description=(
            "Independently verifiable conditions. "
            "Each must be a single, falsifiable statement. "
            "Bad: 'API works correctly.' "
            "Good: 'GET /health returns 200 with body {\"status\":\"ok\"}.'"
        ),
        min_length=1,
    )
    test_plan: TestPlan = Field(
        description="Verification plan: tests to write and commands to run.",
    )
    files_likely_affected: list[str] = Field(
        default_factory=list,
        description="Predicted file paths that will be created or modified.",
    )
    domains: list[str] = Field(
        default_factory=list,
        description=(
            "Work categories for routing and context loading. "
            "Values: go-backend, python-agent, rust-infra, database, api, config, frontend, docs."
        ),
    )


# ---------------------------------------------------------------------------
# Reviewer output
# ---------------------------------------------------------------------------

class ReviewFinding(BaseModel):
    severity: Literal["error", "warning", "info"] = Field(
        description="error = blocks pass, warning = notable but non-blocking, info = observation.",
    )
    file: str = Field(
        default="",
        description="File path the finding refers to, or empty string if general.",
    )
    line: int | None = Field(
        default=None,
        description="Line number if applicable, null otherwise.",
    )
    message: str = Field(
        description="What was found.",
    )
    evidence: str = Field(
        default="",
        description="Concrete proof: test output line, code snippet, or command result.",
    )


class CriterionResult(BaseModel):
    criterion: str = Field(
        description="Exact text of the acceptance criterion from the spec.",
    )
    met: bool = Field(
        description="Whether this criterion is satisfied.",
    )
    evidence: str = Field(
        description="How it was verified: test name that passed, code snippet, command output.",
    )


class ReviewVerdict(_SchemaBase):
    """Review verdict produced by the reviewer agent."""

    verdict: Literal["PASS", "FAIL"] = Field(
        description="PASS only when every acceptance criterion is met and no error-severity findings exist.",
    )
    findings: list[ReviewFinding] = Field(
        description="Observations from the review, categorized by severity.",
    )
    summary: str = Field(
        description="One-paragraph overall assessment of the implementation.",
    )
    criteria_met: list[CriterionResult] = Field(
        description="One entry per acceptance criterion — must cover every criterion from the spec.",
    )


# ---------------------------------------------------------------------------
# Bootstrapper output
# ---------------------------------------------------------------------------

class RuntimeInfo(BaseModel):
    language: str = Field(
        description="Primary language: go, python, rust, typescript, java.",
    )
    version: str = Field(
        description="Language version (e.g. '1.22', '3.12', '1.78').",
    )
    framework: str = Field(
        default="",
        description="Primary framework if any (e.g. gin, fastapi, axum), or empty string.",
    )


class ToolInfo(BaseModel):
    name: str = Field(description="Tool name (e.g. go, cargo, pytest).")
    version: str = Field(description="Tool version.")
    path: str = Field(default="", description="Expected binary path.")


class DependencyInfo(BaseModel):
    name: str = Field(description="Package/crate/module name.")
    version: str = Field(description="Version constraint or exact version.")


class WorkspaceCapabilities(BaseModel):
    runtime: RuntimeInfo = Field(description="Primary language runtime.")
    tools: list[ToolInfo] = Field(description="Available build, test, and lint tools.")
    dependencies: list[DependencyInfo] = Field(description="Key project dependencies.")
    test_command: str = Field(description="Command to run the test suite (e.g. 'go test ./...', 'pytest -v').")
    lint_command: str = Field(description="Command to run the linter (e.g. 'golangci-lint run', 'ruff check .').")
    build_command: str = Field(description="Command to build the project (e.g. 'go build ./...', 'cargo build').")


class BootstrapResult(_SchemaBase):
    """Workspace capabilities produced by the bootstrapper agent."""

    workspace_id: str = Field(
        description="Unique workspace identifier: ws-<short_hash>.",
        pattern=r"^ws-",
    )
    status: Literal["ready"] = Field(
        description="Workspace readiness — always 'ready' on successful analysis.",
    )
    capabilities: WorkspaceCapabilities = Field(
        description="Workspace capabilities: runtime, tools, dependencies, and commands.",
    )


# ---------------------------------------------------------------------------
# Worker tools (Pydantic → OpenAI function schema)
# ---------------------------------------------------------------------------

class WriteFile(_SchemaBase):
    """Create or overwrite a file with the given content."""

    path: str = Field(description="Relative file path (e.g. 'src/health.py').")
    content: str = Field(description="Full file content to write.")


class ReadFile(_SchemaBase):
    """Read the contents of an existing file."""

    path: str = Field(description="Relative file path to read.")


class RunCommand(_SchemaBase):
    """Run a shell command in the project directory. Use for tests, linting, compilation. Returns stdout+stderr."""

    command: str = Field(description="Shell command to execute.")


class ListFiles(_SchemaBase):
    """List files in a directory."""

    path: str = Field(default=".", description="Directory path (default: project root).")


# ---------------------------------------------------------------------------
# Bootstrapper tools (environment management)
# ---------------------------------------------------------------------------

class WebSearch(_SchemaBase):
    """Search the web for information about tools, packages, or MCP servers."""

    query: str = Field(description="Search query (e.g. 'opensearch MCP server npm package').")


class InstallPackage(_SchemaBase):
    """Install a package or tool in the workspace."""

    manager: str = Field(description="Package manager: 'pip', 'npm', 'go', 'cargo', 'apt'.")
    package: str = Field(description="Package name with optional version (e.g. 'opensearch-mcp-server@latest').")


class RegisterMcpServer(_SchemaBase):
    """Register an MCP server for use by other agents in this workspace."""

    name: str = Field(description="MCP server name (e.g. 'opensearch', 'github', 'slack').")
    command: str = Field(description="Command to start the MCP server (e.g. 'npx opensearch-mcp-server').")
    args: list[str] = Field(default_factory=list, description="Command arguments.")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables (e.g. {'OPENSEARCH_URL': 'http://localhost:9200'}).")
    description: str = Field(default="", description="What this MCP server provides.")
