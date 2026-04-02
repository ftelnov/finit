"""Planner agent logic.

Receives a task description and generates a structured specification with
acceptance criteria, test plan, affected files, and domains.
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
You are a senior software engineer acting as a task planner. Given a task \
description, you generate a structured specification for implementation.

You MUST respond with valid JSON (no markdown, no code blocks) in exactly \
this format:

{
  "title": "Short title for the task",
  "description": "Detailed description of what needs to be done",
  "acceptance_criteria": [
    "Criterion 1 - specific and testable",
    "Criterion 2 - specific and testable"
  ],
  "test_plan": {
    "unit_tests": ["TestName1", "TestName2"],
    "commands": ["test command 1"]
  },
  "files_likely_affected": ["file1.go", "file2.go"],
  "domains": ["domain1"]
}

Requirements for the specification:
- Title should be concise but descriptive
- Description should explain the full scope
- Acceptance criteria must be specific and verifiable
- Test plan should include concrete test names and commands
- Files affected should be realistic guesses based on the task
- Domains should categorize the work (e.g., "go-backend", "api", "database")
"""


async def handle_task(
    task_id: str,
    message: Message,
    metadata: dict[str, Any],
) -> A2AResult:
    """Process a planner task: generate a structured spec from task description."""
    # Extract the task description from the message parts
    task_description = ""
    for part in message.parts:
        if part.type == "text" and part.text:
            task_description = part.text
            break

    if not task_description:
        return A2AResult(
            status=TaskStatus(state=TaskState.failed, message="No task description provided"),
            artifacts=[],
        )

    logger.info("Generating spec for task %s: %s", task_id, task_description[:100])

    llm = LLMClient(agent_id="planner")
    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Create a specification for the following task:\n\n{task_description}"},
        ]

        spec = await llm.chat_json(messages, task_id=task_id)

        # Validate that we got the expected fields
        required_fields = ["title", "description", "acceptance_criteria", "test_plan"]
        for field in required_fields:
            if field not in spec:
                spec[field] = "" if field != "acceptance_criteria" else []

        # Ensure list fields are lists
        if not isinstance(spec.get("acceptance_criteria"), list):
            spec["acceptance_criteria"] = [str(spec["acceptance_criteria"])]
        if not isinstance(spec.get("files_likely_affected"), list):
            spec["files_likely_affected"] = []
        if not isinstance(spec.get("domains"), list):
            spec["domains"] = []

        logger.info("Spec generated for task %s: %s", task_id, spec.get("title", "untitled"))

        return A2AResult(
            status=TaskStatus(state=TaskState.completed),
            artifacts=[
                Artifact(
                    parts=[MessagePart(type="text", text=json.dumps(spec, ensure_ascii=False))],
                    metadata={"type": "spec"},
                )
            ],
        )
    except Exception as exc:
        logger.exception("Failed to generate spec for task %s", task_id)
        return A2AResult(
            status=TaskStatus(state=TaskState.failed, message=f"Spec generation failed: {exc}"),
            artifacts=[],
        )
    finally:
        await llm.close()
