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
from finit_agent.llm import LLMClient, load_prompt_versioned

logger = logging.getLogger(__name__)


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
            status=TaskStatus.fail("No task description provided"),
            artifacts=[],
        )

    logger.info("Generating spec for task %s: %s", task_id, task_description[:100])

    # Load versioned prompt (A/B selection from prompt_configs table)
    system_prompt, prompt_version, prompt_params = await load_prompt_versioned("planner")
    logger.info("Planner using prompt version=%s for task %s", prompt_version, task_id)

    llm = LLMClient(agent_id="planner", prompt_version=prompt_version)
    try:
        messages = [
            {"role": "system", "content": system_prompt},
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
            status=TaskStatus.fail(f"Spec generation failed: {exc}"),
            artifacts=[],
        )
    finally:
        await llm.close()
