"""Bootstrapper agent logic.

Receives a spec + project metadata, analyzes requirements via LLM, and
returns workspace capabilities (runtime, tools, dependencies, commands).
For PoC this does not actually create Docker containers - just returns
a capability report.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from finit_agent.a2a import (
    A2AResult,
    Artifact,
    Message,
    MessagePart,
    TaskState,
    TaskStatus,
)
from finit_agent.llm import LLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a workspace bootstrapper agent. Given a task specification and \
optional project metadata, you analyze the requirements and determine the \
workspace capabilities needed.

You MUST respond with valid JSON (no markdown, no code blocks) in exactly \
this format:

{
  "workspace_id": "ws-<short_hash>",
  "status": "ready",
  "capabilities": {
    "runtime": {
      "language": "<language>",
      "version": "<version>",
      "framework": "<framework_or_empty>"
    },
    "tools": [
      {"name": "<tool>", "version": "<version>", "path": "<path>"}
    ],
    "dependencies": [
      {"name": "<dep>", "version": "<version>"}
    ],
    "test_command": "<command to run tests>",
    "lint_command": "<command to run linter>",
    "build_command": "<command to build>"
  }
}

Analyze the spec to determine:
- Primary programming language and version
- Required frameworks and libraries
- Build, test, and lint tools
- Key dependencies
"""


async def handle_task(
    task_id: str,
    message: Message,
    metadata: dict[str, Any],
) -> A2AResult:
    """Process a bootstrapper task: analyze spec and return workspace capabilities."""
    # Extract the payload from the message
    payload_text = ""
    for part in message.parts:
        if part.type == "text" and part.text:
            payload_text = part.text
            break

    if not payload_text:
        return A2AResult(
            status=TaskStatus(state=TaskState.failed, message="No payload provided"),
            artifacts=[],
        )

    logger.info("Analyzing workspace requirements for task %s", task_id)

    llm = LLMClient(agent_id="bootstrapper")
    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Analyze the following specification and determine workspace "
                    f"capabilities needed:\n\n{payload_text}"
                ),
            },
        ]

        capabilities = await llm.chat_json(messages, task_id=task_id)

        # Ensure we have the expected structure
        if "workspace_id" not in capabilities:
            capabilities["workspace_id"] = f"ws-{task_id[:12]}"
        if "status" not in capabilities:
            capabilities["status"] = "ready"
        if "capabilities" not in capabilities:
            capabilities["capabilities"] = {}

        logger.info(
            "Workspace capabilities determined for task %s: lang=%s",
            task_id,
            capabilities.get("capabilities", {}).get("runtime", {}).get("language", "unknown"),
        )

        return A2AResult(
            status=TaskStatus(state=TaskState.completed),
            artifacts=[
                Artifact(
                    parts=[MessagePart(type="text", text=json.dumps(capabilities, ensure_ascii=False))],
                    metadata={"type": "workspace_capabilities"},
                )
            ],
        )
    except Exception as exc:
        logger.exception("Failed to analyze workspace for task %s", task_id)
        return A2AResult(
            status=TaskStatus(state=TaskState.failed, message=f"Workspace analysis failed: {exc}"),
            artifacts=[],
        )
    finally:
        await llm.close()
