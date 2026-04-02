"""A2A (Agent-to-Agent) protocol server implementation.

Implements JSON-RPC 2.0 over HTTP with agent card discovery and health checks.
"""

from __future__ import annotations

import logging
import time
import uuid
from enum import Enum
from typing import Any, Callable, Awaitable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TaskState(str, Enum):
    submitted = "submitted"
    working = "working"
    input_required = "input-required"
    completed = "completed"
    failed = "failed"


class AgentSkill(BaseModel):
    id: str
    name: str
    description: str = ""


class AgentCapabilities(BaseModel):
    streaming: bool = False
    pushNotifications: bool = False


class AgentCard(BaseModel):
    name: str
    description: str
    url: str
    version: str = "1.0.0"
    capabilities: AgentCapabilities = AgentCapabilities()
    skills: list[AgentSkill] = []


class MessagePart(BaseModel):
    type: str = "text"
    text: str = ""


class Message(BaseModel):
    role: str = "user"
    parts: list[MessagePart] = []


class TaskStatus(BaseModel):
    state: TaskState = TaskState.completed
    message: str | None = None


class Artifact(BaseModel):
    parts: list[MessagePart] = []
    metadata: dict[str, Any] = {}


class A2AResult(BaseModel):
    """Result returned by an agent handler."""
    status: TaskStatus = TaskStatus()
    artifacts: list[Artifact] = []


class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: dict[str, Any] = {}
    id: str | int | None = None


class JsonRpcError(BaseModel):
    code: int
    message: str
    data: Any = None


# ---------------------------------------------------------------------------
# Handler type
# ---------------------------------------------------------------------------

TaskHandler = Callable[[str, Message, dict[str, Any]], Awaitable[A2AResult]]


# ---------------------------------------------------------------------------
# JSON-RPC error codes
# ---------------------------------------------------------------------------

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


def _jsonrpc_error(req_id: str | int | None, code: int, message: str, data: Any = None) -> dict:
    resp: dict[str, Any] = {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
        "id": req_id,
    }
    if data is not None:
        resp["error"]["data"] = data
    return resp


def _jsonrpc_result(req_id: str | int | None, result: Any) -> dict:
    return {
        "jsonrpc": "2.0",
        "result": result,
        "id": req_id,
    }


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

_start_time: float = 0.0


def create_a2a_app(agent_card: AgentCard, handler: TaskHandler) -> FastAPI:
    """Create a FastAPI application implementing the A2A protocol.

    Args:
        agent_card: The agent card served at /.well-known/agent.json.
        handler: Async function called for tasks/send.
                 Signature: async def handler(task_id, message, metadata) -> A2AResult
    """
    app = FastAPI(title=f"Finit Agent - {agent_card.name}", version=agent_card.version)
    card_dict = agent_card.model_dump()

    global _start_time
    _start_time = time.time()

    # -- Agent card endpoint --------------------------------------------------

    @app.get("/.well-known/agent.json")
    async def get_agent_card() -> dict:
        return card_dict

    # -- Health check ---------------------------------------------------------

    @app.get("/health")
    async def health() -> dict:
        uptime = time.time() - _start_time
        return {
            "status": "healthy",
            "agent": agent_card.name,
            "uptime_seconds": round(uptime, 2),
        }

    # -- JSON-RPC 2.0 dispatcher ----------------------------------------------

    @app.post("/")
    async def jsonrpc_dispatch(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                _jsonrpc_error(None, PARSE_ERROR, "Parse error"),
                status_code=200,
            )

        # Validate basic JSON-RPC structure
        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or "method" not in body:
            return JSONResponse(
                _jsonrpc_error(body.get("id") if isinstance(body, dict) else None,
                               INVALID_REQUEST, "Invalid request"),
                status_code=200,
            )

        req_id = body.get("id")
        method = body["method"]
        params = body.get("params", {})

        if method == "tasks/send":
            return await _handle_tasks_send(req_id, params, handler)
        elif method == "tasks/get":
            return await _handle_tasks_get(req_id, params)
        elif method == "tasks/cancel":
            return await _handle_tasks_cancel(req_id, params)
        else:
            return JSONResponse(
                _jsonrpc_error(req_id, METHOD_NOT_FOUND, f"Method not found: {method}"),
                status_code=200,
            )

    return app


async def _handle_tasks_send(
    req_id: str | int | None,
    params: dict[str, Any],
    handler: TaskHandler,
) -> JSONResponse:
    """Handle tasks/send: delegate to the agent handler and return result."""
    task_id = params.get("id", str(uuid.uuid4()))
    raw_message = params.get("message", {})
    metadata = params.get("metadata", {})

    # Parse the message
    try:
        message = Message(**raw_message)
    except Exception:
        message = Message(role="user", parts=[MessagePart(type="text", text=str(raw_message))])

    try:
        logger.info("tasks/send task_id=%s", task_id)
        result = await handler(task_id, message, metadata)

        return JSONResponse(
            _jsonrpc_result(req_id, {
                "id": task_id,
                "status": result.status.model_dump(),
                "artifacts": [a.model_dump() for a in result.artifacts],
            }),
            status_code=200,
        )
    except Exception as exc:
        logger.exception("Handler error for task %s", task_id)
        return JSONResponse(
            _jsonrpc_result(req_id, {
                "id": task_id,
                "status": {"state": "failed", "message": str(exc)},
                "artifacts": [],
            }),
            status_code=200,
        )


async def _handle_tasks_get(req_id: str | int | None, params: dict[str, Any]) -> JSONResponse:
    """Handle tasks/get (stub for PoC)."""
    task_id = params.get("id", "unknown")
    return JSONResponse(
        _jsonrpc_result(req_id, {
            "id": task_id,
            "status": {"state": "completed"},
            "artifacts": [],
        }),
        status_code=200,
    )


async def _handle_tasks_cancel(req_id: str | int | None, params: dict[str, Any]) -> JSONResponse:
    """Handle tasks/cancel (stub for PoC)."""
    task_id = params.get("id", "unknown")
    return JSONResponse(
        _jsonrpc_result(req_id, {
            "id": task_id,
            "status": {"state": "failed", "message": "cancelled"},
            "artifacts": [],
        }),
        status_code=200,
    )
