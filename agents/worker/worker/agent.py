"""Worker agent logic.

Receives a spec + workspace capabilities, calls the LLM to generate code,
and returns artifacts (code changes, test results).
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
You are a senior software engineer acting as a code worker. Given a task \
specification and workspace capabilities, you generate the code changes \
needed to implement the task.

You MUST respond with valid JSON (no markdown, no code blocks) in exactly \
this format:

{
  "artifacts": [
    {
      "type": "code_change",
      "path": "<file_path>",
      "content": "<full file content>",
      "action": "create" | "modify"
    }
  ],
  "test_results": {
    "command": "<test command executed>",
    "exit_code": 0,
    "stdout": "<test output>",
    "stderr": ""
  },
  "summary": "Brief summary of changes made"
}

Requirements:
- Generate complete, working code (no placeholders or TODOs)
- Include all necessary imports
- Write tests that cover acceptance criteria
- Follow the conventions of the target language/framework
- Use workspace capabilities to determine available tools
"""


async def handle_task(
    task_id: str,
    message: Message,
    metadata: dict[str, Any],
) -> A2AResult:
    """Process a worker task: generate code based on spec and workspace capabilities."""
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

    logger.info("Generating code for task %s", task_id)

    llm = LLMClient(agent_id="worker")
    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Implement the following specification. Generate complete "
                    f"code changes and tests:\n\n{payload_text}"
                ),
            },
        ]

        result = await llm.chat_json(messages, task_id=task_id)

        # Validate structure
        if "artifacts" not in result:
            result["artifacts"] = []
        if "test_results" not in result:
            result["test_results"] = {
                "command": "N/A",
                "exit_code": 0,
                "stdout": "No tests executed",
                "stderr": "",
            }
        if "summary" not in result:
            result["summary"] = "Code changes generated"

        logger.info(
            "Code generated for task %s: %d artifacts, summary=%s",
            task_id,
            len(result.get("artifacts", [])),
            result.get("summary", ""),
        )

        return A2AResult(
            status=TaskStatus(state=TaskState.completed),
            artifacts=[
                Artifact(
                    parts=[MessagePart(type="text", text=json.dumps(result, ensure_ascii=False))],
                    metadata={"type": "code_artifacts"},
                )
            ],
        )
    except Exception as exc:
        logger.exception("Failed to generate code for task %s", task_id)
        return A2AResult(
            status=TaskStatus(state=TaskState.failed, message=f"Code generation failed: {exc}"),
            artifacts=[],
        )
    finally:
        await llm.close()
