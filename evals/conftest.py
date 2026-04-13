"""Shared fixtures for eval tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from fixtures import REPO_GENERATORS, RepoFixture


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

LLM_URL = os.environ.get("LLM_ROUTER_URL", "http://localhost:8081")
LLM_MODEL = os.environ.get("LLM_MODEL", "/opt/MiniMaxAI/MiniMax-M2.7")

AGENT_URLS = {
    "planner": os.environ.get("PLANNER_URL", "http://localhost:9000"),
    "bootstrapper": os.environ.get("BOOTSTRAPPER_URL", "http://localhost:9001"),
    "worker": os.environ.get("WORKER_URL", "http://localhost:9002"),
    "reviewer": os.environ.get("REVIEWER_URL", "http://localhost:9003"),
}

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8080")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def client():
    """Shared httpx client with generous timeout for real LLM calls."""
    with httpx.Client(timeout=180.0) as c:
        yield c


@pytest.fixture(scope="session")
def async_client():
    return httpx.AsyncClient(timeout=180.0)


@pytest.fixture
def make_repo(tmp_path):
    """Factory fixture: call with repo_type to get a RepoFixture."""
    def _make(repo_type: str) -> RepoFixture:
        gen = REPO_GENERATORS[repo_type]
        return gen(tmp_path / repo_type)
    return _make


# ---------------------------------------------------------------------------
# A2A helpers
# ---------------------------------------------------------------------------

def a2a_send(
    client: httpx.Client,
    agent: str,
    task_id: str,
    payload: dict[str, Any],
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Send an A2A task to an agent and return the parsed result."""
    url = AGENT_URLS[agent]
    resp = client.post(
        url,
        json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "params": {
                "id": task_id,
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": json.dumps(payload)}],
                },
                "metadata": {},
            },
            "id": f"{agent}-{task_id}",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()

    # Extract result
    result = data.get("result", {})
    state = result.get("status", {}).get("state", "unknown")
    if state != "completed":
        msg = result.get("status", {}).get("message", "no message")
        raise RuntimeError(f"Agent {agent} returned state={state}: {msg}")

    # Parse the artifact text as JSON
    artifacts = result.get("artifacts", [])
    if not artifacts:
        raise RuntimeError(f"Agent {agent} returned no artifacts")

    text = artifacts[0].get("parts", [{}])[0].get("text", "")
    return json.loads(text)


def build_project_context(repo: RepoFixture) -> dict[str, Any]:
    """Build a project context dict from a repo fixture for agent consumption."""
    return {
        "project_root": str(repo.path),
        "language": repo.language,
        "framework": repo.framework,
        "file_tree": repo.tree(),
        "files": repo.file_contents_summary(),
    }
