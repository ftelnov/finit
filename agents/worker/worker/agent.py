"""Worker agent logic — tool-based agentic loop.

Instead of generating code as a single JSON blob, the worker uses tools
(write_file, read_file, run_command) to actually write code, run tests,
and iterate on feedback from the compiler/linter until the task is done.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
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
from finit_agent.schemas import ListFiles, ReadFile, RunCommand, WriteFile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tools available to the worker LLM
# ---------------------------------------------------------------------------

WORKER_TOOLS = [
    WriteFile.tool_schema(),
    ReadFile.tool_schema(),
    RunCommand.tool_schema(),
    ListFiles.tool_schema(),
]


# ---------------------------------------------------------------------------
# Tool executor
# ---------------------------------------------------------------------------

class WorkspaceExecutor:
    """Executes tool calls in a sandboxed workspace directory."""

    def __init__(self, workspace_dir: Path, project_files: dict[str, str] | None = None):
        self.workspace_dir = workspace_dir
        self.written_files: dict[str, str] = {}  # track what we wrote
        self.commands_run: list[dict[str, Any]] = []

        # Seed workspace with existing project files
        if project_files:
            for rel_path, content in project_files.items():
                fp = workspace_dir / rel_path
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(content)

    async def execute(self, name: str, args: dict[str, Any]) -> str:
        try:
            if name == "write_file":
                validated = WriteFile.model_validate(args)
                return self._write_file(validated.path, validated.content)
            elif name == "read_file":
                validated = ReadFile.model_validate(args)
                return self._read_file(validated.path)
            elif name == "run_command":
                validated = RunCommand.model_validate(args)
                return self._run_command(validated.command)
            elif name == "list_files":
                validated = ListFiles.model_validate(args)
                return self._list_files(validated.path)
            else:
                return f"Unknown tool: {name}"
        except Exception as exc:
            return f"ERROR: invalid tool args: {exc}"

    def _write_file(self, path: str, content: str) -> str:
        fp = self.workspace_dir / path
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        self.written_files[path] = content
        return f"OK: wrote {len(content)} bytes to {path}"

    def _read_file(self, path: str) -> str:
        fp = self.workspace_dir / path
        if not fp.exists():
            return f"ERROR: file not found: {path}"
        if not fp.is_file():
            return f"ERROR: not a file: {path}"
        content = fp.read_text()
        if len(content) > 10000:
            return content[:10000] + f"\n... (truncated, {len(content)} bytes total)"
        return content

    def _run_command(self, command: str) -> str:
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                output += result.stderr
            if not output:
                output = "(no output)"
            entry = {
                "command": command,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
            self.commands_run.append(entry)
            return f"exit_code={result.returncode}\n{output}"
        except subprocess.TimeoutExpired:
            return "ERROR: command timed out after 60s"
        except Exception as exc:
            return f"ERROR: {exc}"

    def _list_files(self, path: str) -> str:
        dir_path = self.workspace_dir / path
        if not dir_path.exists():
            return f"ERROR: directory not found: {path}"
        files = []
        for root, dirs, fnames in os.walk(dir_path):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules", "target", ".venv")]
            for f in sorted(fnames):
                fp = Path(root) / f
                rel = str(fp.relative_to(self.workspace_dir))
                files.append(rel)
        return "\n".join(files) if files else "(empty)"


# ---------------------------------------------------------------------------
# Agent handler
# ---------------------------------------------------------------------------

async def handle_task(
    task_id: str,
    message: Message,
    metadata: dict[str, Any],
) -> A2AResult:
    """Run the worker agentic loop: write code, test, iterate."""
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

    # Parse payload
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        payload = {"spec": {"description": payload_text}}

    spec = payload.get("spec", {})
    workspace = payload.get("workspace", {})
    project = payload.get("project", {})
    project_files = project.get("files", {})

    logger.info("Worker starting agentic loop for task %s", task_id)

    # Create a temp workspace
    workspace_dir = Path(tempfile.mkdtemp(prefix=f"finit-worker-{task_id[:8]}-"))
    executor = WorkspaceExecutor(workspace_dir, project_files)

    # Load versioned prompt (A/B selection from prompt_configs table)
    system_prompt, prompt_version, prompt_params = await load_prompt_versioned("worker")
    logger.info("Worker using prompt version=%s for task %s", prompt_version, task_id)

    llm = LLMClient(agent_id="worker", prompt_version=prompt_version)
    try:
        # Build the user message with full context
        caps = workspace.get("capabilities", workspace)
        user_msg = (
            f"## Task Specification\n{json.dumps(spec, indent=2)}\n\n"
            f"## Workspace Capabilities\n{json.dumps(caps, indent=2)}\n\n"
            f"## Existing Project Files\n"
        )
        for fpath in sorted(project_files.keys()):
            user_msg += f"\n### {fpath}\n```\n{project_files[fpath]}\n```\n"

        user_msg += (
            "\n## Instructions\n"
            "Implement the specification. Start by reading the existing files, "
            "then write your changes, then run the tests to verify. "
            "Fix any errors. When done, reply with a summary."
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]

        final_msg = await llm.chat_tools(
            messages=messages,
            tools=WORKER_TOOLS,
            execute_tool=executor.execute,
            task_id=task_id,
        )

        # Build result from what the executor actually did
        artifacts = []
        for path, content in executor.written_files.items():
            artifacts.append({
                "type": "code_change",
                "path": path,
                "content": content,
                "action": "create",
            })

        # Use the last test run as test_results
        test_results = {"command": "N/A", "exit_code": 0, "stdout": "", "stderr": ""}
        for cmd_entry in reversed(executor.commands_run):
            if "test" in cmd_entry["command"].lower() or "pytest" in cmd_entry["command"].lower():
                test_results = cmd_entry
                break

        # Final assistant text is the summary
        summary = ""
        if final_msg.get("content"):
            from finit_agent.llm import _strip_think_tags
            summary = _strip_think_tags(final_msg["content"])

        result = {
            "artifacts": artifacts,
            "test_results": test_results,
            "summary": summary or "Code changes implemented via tool loop",
        }

        logger.info(
            "Worker finished for task %s: %d files written, %d commands run",
            task_id, len(executor.written_files), len(executor.commands_run),
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
        logger.exception("Worker failed for task %s", task_id)
        return A2AResult(
            status=TaskStatus.fail(f"Code generation failed: {exc}"),
            artifacts=[],
        )
    finally:
        await llm.close()
        # Clean up workspace
        shutil.rmtree(workspace_dir, ignore_errors=True)
