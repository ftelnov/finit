"""Reviewer agent logic.

Receives a spec + artifacts from the worker, calls the LLM to evaluate
whether the artifacts satisfy the spec, and returns a structured review
with verdict (PASS/FAIL), findings, and evidence.
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
    """Process a reviewer task: evaluate artifacts against spec."""
    # Extract the payload from the message
    payload_text = ""
    for part in message.parts:
        if part.type == "text" and part.text:
            payload_text = part.text
            break

    if not payload_text:
        return A2AResult(
            status=TaskStatus.fail("No payload provided"),
            artifacts=[],
        )

    logger.info("Reviewing artifacts for task %s", task_id)

    # Load versioned prompt (A/B selection from prompt_configs table)
    system_prompt, prompt_version, prompt_params = await load_prompt_versioned("reviewer")
    logger.info("Reviewer using prompt version=%s for task %s", prompt_version, task_id)

    llm = LLMClient(agent_id="reviewer", prompt_version=prompt_version)
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Review the following implementation against the specification. "
                    f"Determine if all acceptance criteria are met:\n\n{payload_text}"
                ),
            },
        ]

        review = await llm.chat_json(messages, task_id=task_id)

        # Validate structure
        if "verdict" not in review:
            review["verdict"] = "PASS"
        if "findings" not in review:
            review["findings"] = []
        if "summary" not in review:
            review["summary"] = "Review completed"
        if "criteria_met" not in review:
            review["criteria_met"] = []

        # Ensure verdict is valid
        if review["verdict"] not in ("PASS", "FAIL"):
            review["verdict"] = "FAIL"

        logger.info(
            "Review completed for task %s: verdict=%s, findings=%d",
            task_id,
            review["verdict"],
            len(review.get("findings", [])),
        )

        return A2AResult(
            status=TaskStatus(state=TaskState.completed),
            artifacts=[
                Artifact(
                    parts=[MessagePart(type="text", text=json.dumps(review, ensure_ascii=False))],
                    metadata={"type": "review"},
                )
            ],
        )
    except Exception as exc:
        logger.exception("Failed to review artifacts for task %s", task_id)
        return A2AResult(
            status=TaskStatus.fail(f"Review failed: {exc}"),
            artifacts=[],
        )
    finally:
        await llm.close()
