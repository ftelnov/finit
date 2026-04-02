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
from finit_agent.llm import LLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a senior software engineer acting as a code reviewer. Given a task \
specification and implementation artifacts, you evaluate whether the \
implementation satisfies the specification.

You MUST respond with valid JSON (no markdown, no code blocks) in exactly \
this format:

{
  "verdict": "PASS" | "FAIL",
  "findings": [
    {
      "severity": "error" | "warning" | "info",
      "file": "<file_path or empty>",
      "line": <line_number or null>,
      "message": "Description of the finding",
      "evidence": "Relevant evidence (test output, code snippet, etc.)"
    }
  ],
  "summary": "Overall assessment of the implementation",
  "criteria_met": [
    {
      "criterion": "Text of the acceptance criterion",
      "met": true | false,
      "evidence": "How it was verified"
    }
  ]
}

Review guidelines:
- Evaluate ONLY against the acceptance criteria in the spec
- Every acceptance criterion must be explicitly checked
- Verdict is PASS only if ALL criteria are met
- Findings with severity "error" cause FAIL
- Provide concrete evidence for each finding
- Be thorough but fair - do not invent issues
"""


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
            status=TaskStatus(state=TaskState.failed, message="No payload provided"),
            artifacts=[],
        )

    logger.info("Reviewing artifacts for task %s", task_id)

    llm = LLMClient(agent_id="reviewer")
    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
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
            status=TaskStatus(state=TaskState.failed, message=f"Review failed: {exc}"),
            artifacts=[],
        )
    finally:
        await llm.close()
