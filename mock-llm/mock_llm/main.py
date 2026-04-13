"""Mock LLM server - OpenAI-compatible API for testing.

Inspects incoming messages to determine the kind of response to generate.
Returns predictable, well-structured responses for each agent type.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Finit Mock LLM", version="0.1.0")

_start_time = time.time()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "mock-llm"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    stop: list[str] | str | None = None
    response_format: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Canned responses
# ---------------------------------------------------------------------------

SPEC_RESPONSE = json.dumps({
    "title": "Add health check endpoint",
    "description": "Add a GET /healthz endpoint that returns 200 OK with uptime information. "
                   "The endpoint should be registered on the main router and return a JSON response "
                   "with status and uptime_seconds fields.",
    "acceptance_criteria": [
        "GET /healthz returns 200 with JSON {\"status\": \"ok\", \"uptime_seconds\": <int>}",
        "Uptime is calculated from server start time",
        "Endpoint is registered on the main router",
        "Unit test covers response format and status code"
    ],
    "test_plan": {
        "unit_tests": ["TestHealthzReturns200", "TestHealthzResponseFormat", "TestHealthzUptime"],
        "commands": ["go test ./... -v -run TestHealthz"]
    },
    "files_likely_affected": ["handler.go", "handler_test.go", "main.go"],
    "domains": ["go-backend", "api"]
}, ensure_ascii=False)

REVIEW_RESPONSE = json.dumps({
    "verdict": "PASS",
    "findings": [
        {
            "severity": "info",
            "file": "handler.go",
            "line": None,
            "message": "Implementation follows project conventions",
            "evidence": "Code uses standard library patterns consistent with existing codebase"
        }
    ],
    "summary": "All acceptance criteria are met. The implementation is correct, tests pass, "
               "and code follows project conventions.",
    "criteria_met": [
        {
            "criterion": "GET /healthz returns 200 with JSON response",
            "met": True,
            "evidence": "Test TestHealthzReturns200 passes - endpoint returns 200 with correct JSON"
        },
        {
            "criterion": "Uptime is calculated from server start time",
            "met": True,
            "evidence": "Server start time is captured at init, uptime calculated on each request"
        },
        {
            "criterion": "Unit test covers response format and status code",
            "met": True,
            "evidence": "TestHealthzResponseFormat validates JSON structure and TestHealthzReturns200 checks status code"
        }
    ]
}, ensure_ascii=False)

WORKSPACE_RESPONSE = json.dumps({
    "workspace_id": "ws-abc123def456",
    "status": "ready",
    "capabilities": {
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
}, ensure_ascii=False)

CODE_RESPONSE = json.dumps({
    "artifacts": [
        {
            "type": "code_change",
            "path": "handler.go",
            "content": (
                "package main\n\n"
                "import (\n"
                "\t\"encoding/json\"\n"
                "\t\"net/http\"\n"
                "\t\"time\"\n"
                ")\n\n"
                "var startTime = time.Now()\n\n"
                "type HealthResponse struct {\n"
                "\tStatus        string `json:\"status\"`\n"
                "\tUptimeSeconds int    `json:\"uptime_seconds\"`\n"
                "}\n\n"
                "func healthzHandler(w http.ResponseWriter, r *http.Request) {\n"
                "\tuptime := int(time.Since(startTime).Seconds())\n"
                "\tresp := HealthResponse{\n"
                "\t\tStatus:        \"ok\",\n"
                "\t\tUptimeSeconds: uptime,\n"
                "\t}\n"
                "\tw.Header().Set(\"Content-Type\", \"application/json\")\n"
                "\tw.WriteHeader(http.StatusOK)\n"
                "\tjson.NewEncoder(w).Encode(resp)\n"
                "}\n"
            ),
            "action": "create"
        },
        {
            "type": "code_change",
            "path": "handler_test.go",
            "content": (
                "package main\n\n"
                "import (\n"
                "\t\"encoding/json\"\n"
                "\t\"net/http\"\n"
                "\t\"net/http/httptest\"\n"
                "\t\"testing\"\n"
                ")\n\n"
                "func TestHealthzReturns200(t *testing.T) {\n"
                "\treq := httptest.NewRequest(http.MethodGet, \"/healthz\", nil)\n"
                "\tw := httptest.NewRecorder()\n"
                "\thealthzHandler(w, req)\n"
                "\tif w.Code != http.StatusOK {\n"
                "\t\tt.Errorf(\"expected 200, got %d\", w.Code)\n"
                "\t}\n"
                "}\n\n"
                "func TestHealthzResponseFormat(t *testing.T) {\n"
                "\treq := httptest.NewRequest(http.MethodGet, \"/healthz\", nil)\n"
                "\tw := httptest.NewRecorder()\n"
                "\thealthzHandler(w, req)\n"
                "\tvar resp HealthResponse\n"
                "\terr := json.NewDecoder(w.Body).Decode(&resp)\n"
                "\tif err != nil {\n"
                "\t\tt.Fatalf(\"failed to decode response: %v\", err)\n"
                "\t}\n"
                "\tif resp.Status != \"ok\" {\n"
                "\t\tt.Errorf(\"expected status 'ok', got '%s'\", resp.Status)\n"
                "\t}\n"
                "\tif resp.UptimeSeconds < 0 {\n"
                "\t\tt.Errorf(\"expected non-negative uptime, got %d\", resp.UptimeSeconds)\n"
                "\t}\n"
                "}\n"
            ),
            "action": "create"
        }
    ],
    "test_results": {
        "command": "go test ./... -v -run TestHealthz",
        "exit_code": 0,
        "stdout": "=== RUN   TestHealthzReturns200\n--- PASS: TestHealthzReturns200 (0.00s)\n"
                  "=== RUN   TestHealthzResponseFormat\n--- PASS: TestHealthzResponseFormat (0.00s)\n"
                  "PASS\nok  \tmain\t0.003s",
        "stderr": ""
    },
    "summary": "Added healthz endpoint with handler and tests"
}, ensure_ascii=False)

SUPERVISOR_RESPONSE = json.dumps({
    "action": "call_agent",
    "agent_id": "planner",
    "payload": {
        "task_description": "Generate specification for the given task"
    },
    "reasoning": "The task needs a structured specification before implementation can begin. "
                 "Delegating to the planner agent to create acceptance criteria and test plan."
}, ensure_ascii=False)

DEFAULT_RESPONSE = "I understand your request. This is a mock response from the Finit Mock LLM server."


# ---------------------------------------------------------------------------
# Response routing
# ---------------------------------------------------------------------------

def _detect_response_type(messages: list[ChatMessage]) -> str:
    """Inspect messages to determine what kind of response to generate."""
    all_text = " ".join(m.content.lower() for m in messages)

    # Order matters: more specific patterns first
    if "decide" in all_text or "supervisor" in all_text or "next_action" in all_text:
        return "supervisor"
    if "review" in all_text and ("spec" in all_text or "acceptance" in all_text or "artifact" in all_text):
        return "review"
    if "spec" in all_text or "specification" in all_text or "acceptance_criteria" in all_text:
        return "spec"
    if "workspace" in all_text or "bootstrap" in all_text or "capabilities" in all_text:
        return "workspace"
    if "code" in all_text or "implement" in all_text or "generate" in all_text:
        return "code"

    return "default"


def _get_response_content(response_type: str) -> str:
    """Get the canned response content for the given type."""
    responses = {
        "spec": SPEC_RESPONSE,
        "review": REVIEW_RESPONSE,
        "workspace": WORKSPACE_RESPONSE,
        "code": CODE_RESPONSE,
        "supervisor": SUPERVISOR_RESPONSE,
        "default": DEFAULT_RESPONSE,
    }
    return responses.get(response_type, DEFAULT_RESPONSE)


def _count_tokens(text: str) -> int:
    """Rough token count approximation (1 token ~ 4 chars)."""
    return max(1, len(text) // 4)


def _build_response(
    content: str,
    model: str,
    prompt_tokens: int,
) -> dict[str, Any]:
    """Build a complete OpenAI-format chat completion response."""
    completion_tokens = _count_tokens(content)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------

async def _stream_response(content: str, model: str) -> Any:
    """Generate SSE chunks mimicking OpenAI streaming format."""
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    # Initial chunk with role
    initial = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": ""},
                "finish_reason": None,
            }
        ],
    }
    yield f"data: {json.dumps(initial)}\n\n"

    # Content chunks (split into ~20 char pieces for realistic streaming)
    chunk_size = 20
    for i in range(0, len(content), chunk_size):
        piece = content[i:i + chunk_size]
        chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": piece},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(chunk)}\n\n"

    # Final chunk
    final = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    }
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    """Handle chat completion requests (OpenAI-compatible)."""
    body = await request.json()

    try:
        chat_req = ChatRequest(**body)
    except Exception as exc:
        return JSONResponse(
            {"error": {"message": str(exc), "type": "invalid_request_error"}},
            status_code=400,
        )

    # Determine response type
    response_type = _detect_response_type(chat_req.messages)
    content = _get_response_content(response_type)

    agent_id = request.headers.get("X-Agent-ID", "unknown")
    task_id = request.headers.get("X-Task-ID", "unknown")

    logger.info(
        "Chat completion: model=%s type=%s agent=%s task=%s stream=%s",
        chat_req.model,
        response_type,
        agent_id,
        task_id,
        chat_req.stream,
    )

    if chat_req.stream:
        return StreamingResponse(
            _stream_response(content, chat_req.model),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Calculate prompt tokens from all messages
    prompt_tokens = sum(_count_tokens(m.content) for m in chat_req.messages)

    return JSONResponse(_build_response(content, chat_req.model, prompt_tokens))


@app.get("/v1/models")
async def list_models() -> dict:
    """List available models."""
    return {
        "object": "list",
        "data": [
            {
                "id": "mock-llm",
                "object": "model",
                "created": int(_start_time),
                "owned_by": "finit",
                "permission": [],
                "root": "mock-llm",
                "parent": None,
            }
        ],
    }


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    uptime = time.time() - _start_time
    return {
        "status": "healthy",
        "service": "mock-llm",
        "uptime_seconds": round(uptime, 2),
    }


if __name__ == "__main__":
    uvicorn.run(
        "mock_llm.main:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
