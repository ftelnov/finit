"""Smoke test: verify the real LLM responds correctly to agent prompts.

This test calls the LLM directly (no agents, no router) to verify:
1. The LLM is reachable
2. It returns valid JSON for the bootstrapper prompt
3. It correctly detects project types from repo context

Run standalone:
    LLM_URL=http://10.70.2.11:8006 pytest test_smoke_llm.py -v -s
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx
import pytest

logger = logging.getLogger(__name__)

LLM_URL = os.environ.get("LLM_URL", "http://10.70.2.11:8006")
LLM_MODEL = os.environ.get("LLM_MODEL", "/opt/MiniMaxAI/MiniMax-M2.7")

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _extract_json(content: str) -> dict[str, Any]:
    text = content.strip()
    text = _THINK_RE.sub("", text).strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


BOOTSTRAPPER_SYSTEM = (
    "You are a workspace bootstrapper agent. Given a task specification and "
    "optional project metadata, you analyze the requirements and determine the "
    "workspace capabilities needed.\n\n"
    'You MUST respond with valid JSON (no markdown, no code blocks) in exactly '
    "this format:\n\n"
    '{"workspace_id": "ws-<short_hash>", "status": "ready", "capabilities": '
    '{"runtime": {"language": "<language>", "version": "<version>", '
    '"framework": "<framework_or_empty>"}, '
    '"tools": [{"name": "<tool>", "version": "<version>", "path": "<path>"}], '
    '"dependencies": [{"name": "<dep>", "version": "<version>"}], '
    '"test_command": "<command to run tests>", '
    '"lint_command": "<command to run linter>", '
    '"build_command": "<command to build>"}}'
)


@pytest.fixture(scope="module")
def llm_client():
    with httpx.Client(timeout=120.0) as c:
        yield c


def _call_llm(client: httpx.Client, user_msg: str) -> dict[str, Any]:
    resp = client.post(
        f"{LLM_URL}/v1/chat/completions",
        json={
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": BOOTSTRAPPER_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": 2048,
            "temperature": 0.1,
        },
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    logger.info("Raw LLM response:\n%s", content[:500])
    return _extract_json(content)


class TestSmokeLLMReachable:
    def test_models_endpoint(self, llm_client):
        resp = llm_client.get(f"{LLM_URL}/v1/models")
        assert resp.status_code == 200
        models = resp.json()["data"]
        model_ids = [m["id"] for m in models]
        assert LLM_MODEL in model_ids, f"Model {LLM_MODEL} not in {model_ids}"


class TestSmokeBootstrapperPrompt:
    """Direct LLM calls with bootstrapper prompts for different project types."""

    @pytest.mark.parametrize("project_desc,expected_lang", [
        (
            json.dumps({
                "spec": {"title": "Add health endpoint", "description": "Add /health route"},
                "project": {
                    "language": "python", "framework": "flask",
                    "files": {"requirements.txt": "flask==3.0.3\npytest==8.2.0", "app.py": "from flask import Flask"},
                },
            }),
            "python",
        ),
        (
            json.dumps({
                "spec": {"title": "Add healthz endpoint", "description": "Add /healthz route"},
                "project": {
                    "language": "go", "framework": "chi",
                    "files": {"go.mod": "module example.com/demo\n\ngo 1.22.0\n\nrequire github.com/go-chi/chi/v5 v5.0.12"},
                },
            }),
            "go",
        ),
        (
            json.dumps({
                "spec": {"title": "Add health route", "description": "Add /health route"},
                "project": {
                    "language": "javascript", "framework": "express",
                    "files": {"package.json": '{"dependencies": {"express": "^4.19.0"}, "scripts": {"test": "jest"}}'},
                },
            }),
            "javascript",
        ),
    ], ids=["python-flask", "go-chi", "node-express"])
    def test_detects_language(self, llm_client, project_desc, expected_lang):
        """LLM correctly identifies the project language."""
        result = _call_llm(
            llm_client,
            f"Analyze the following specification and determine workspace capabilities needed:\n\n{project_desc}",
        )

        caps = result.get("capabilities", result)
        runtime = caps.get("runtime", {})
        detected = runtime.get("language", "").lower()

        acceptable = {
            "python": {"python", "py", "python3"},
            "go": {"go", "golang"},
            "javascript": {"javascript", "js", "node", "nodejs", "node.js", "typescript"},
        }

        assert detected in acceptable.get(expected_lang, {expected_lang}), (
            f"Expected {expected_lang}, got {detected!r}. Full response: {json.dumps(result, indent=2)}"
        )

        # Also check it has the basic structure
        assert result.get("workspace_id") or result.get("capabilities"), (
            f"Missing workspace_id or capabilities: {json.dumps(result, indent=2)}"
        )
        print(f"\n  {expected_lang}: detected={detected}, framework={runtime.get('framework', '?')}, "
              f"test_cmd={caps.get('test_command', '?')}")
