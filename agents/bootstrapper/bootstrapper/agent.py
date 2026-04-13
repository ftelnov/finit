"""Bootstrapper agent logic — tool-based environment setup.

The bootstrapper can operate in two modes:
1. **Schema-guided** (default): single-shot LLM call → JSON capabilities report.
2. **Tool-augmented** (when task needs external services): uses web_search,
   install_package, run_command, register_mcp_server to discover and set up
   tools before returning capabilities.

The LLM decides which mode based on the task requirements.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any

import httpx

from finit_agent.a2a import (
    A2AResult,
    Artifact,
    Message,
    MessagePart,
    TaskState,
    TaskStatus,
)
from finit_agent.llm import LLMClient, load_prompt_versioned
from finit_agent.schemas import (
    InstallPackage,
    RegisterMcpServer,
    RunCommand,
    WebSearch,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tools available to the bootstrapper LLM
# ---------------------------------------------------------------------------

BOOTSTRAPPER_TOOLS = [
    WebSearch.tool_schema(),
    InstallPackage.tool_schema(),
    RunCommand.tool_schema(name="run_command"),
    RegisterMcpServer.tool_schema(),
]

# ---------------------------------------------------------------------------
# Tool executor
# ---------------------------------------------------------------------------

class BootstrapExecutor:
    """Executes bootstrapper tools."""

    def __init__(self):
        self.installed_packages: list[str] = []
        self.mcp_servers: list[dict[str, Any]] = []
        self.commands_run: list[dict[str, Any]] = []
        self._http = httpx.Client(timeout=30.0)

    async def execute(self, name: str, args: dict[str, Any]) -> str:
        try:
            if name == "web_search":
                validated = WebSearch.model_validate(args)
                return self._web_search(validated.query)
            elif name == "install_package":
                validated = InstallPackage.model_validate(args)
                return self._install_package(validated.manager, validated.package)
            elif name == "run_command":
                validated = RunCommand.model_validate(args)
                return self._run_command(validated.command)
            elif name == "register_mcp_server":
                validated = RegisterMcpServer.model_validate(args)
                return self._register_mcp_server(
                    validated.name, validated.command, validated.args,
                    validated.env, validated.description,
                )
            else:
                return f"Unknown tool: {name}"
        except Exception as exc:
            return f"ERROR: {exc}"

    def _web_search(self, query: str) -> str:
        """Search via DuckDuckGo Lite (no API key needed)."""
        try:
            import re as _re

            resp = self._http.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (compatible; Finit/1.0)"},
                follow_redirects=True,
            )
            if resp.status_code != 200:
                return f"Search failed: HTTP {resp.status_code}"

            # DuckDuckGo Lite returns results as <td> cells in sequence:
            # title (with link), snippet, url. Parse them from raw td content.
            tds = _re.findall(r'<td[^>]*>(.*?)</td>', resp.text, _re.DOTALL)
            # Clean HTML tags from each cell
            cells = [_re.sub(r'<[^>]+>', '', td).strip() for td in tds]
            # Filter out empty/whitespace cells
            cells = [
                c.replace("&nbsp;", " ").replace("&#x27;", "'").replace("&amp;", "&").strip()
                for c in cells
                if len(c) > 5 and c.replace("&nbsp;", "").strip()
            ]

            # Group into results: (title, snippet, url)
            results = []
            i = 0
            while i < len(cells) and len(results) < 5:
                title = cells[i]
                snippet = cells[i + 1] if i + 1 < len(cells) else ""
                url = cells[i + 2] if i + 2 < len(cells) else ""
                # URL cell starts with a domain
                if url and ("." in url) and not url.startswith("http"):
                    url = f"https://{url}"
                results.append(f"- {title}\n  {url}\n  {snippet}")
                i += 3

            if results:
                return "\n\n".join(results)
            return "No results found. Try a different query."
        except Exception as e:
            return f"Search error: {e}"

    def _install_package(self, manager: str, package: str) -> str:
        """Install a package via the specified manager."""
        cmd_map = {
            "pip": f"pip install {package}",
            "npm": f"npm install -g {package}",
            "go": f"go install {package}",
            "cargo": f"cargo install {package}",
            "apt": f"apt-get install -y {package}",
        }
        cmd = cmd_map.get(manager)
        if not cmd:
            return f"Unknown package manager: {manager}"

        result = self._run_command(cmd)
        if "ERROR" not in result:
            self.installed_packages.append(f"{manager}:{package}")
        return result

    def _run_command(self, command: str) -> str:
        """Run a shell command."""
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=60,
            )
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                output += result.stderr
            if not output:
                output = "(no output)"
            self.commands_run.append({
                "command": command,
                "exit_code": result.returncode,
            })
            return f"exit_code={result.returncode}\n{output[:2000]}"
        except subprocess.TimeoutExpired:
            return "ERROR: command timed out after 60s"
        except Exception as exc:
            return f"ERROR: {exc}"

    def _register_mcp_server(
        self, name: str, command: str, args: list[str],
        env: dict[str, str], description: str,
    ) -> str:
        """Register an MCP server configuration."""
        server = {
            "name": name,
            "command": command,
            "args": args,
            "env": env,
            "description": description,
        }
        self.mcp_servers.append(server)

        # Also try to register with the orchestrator if available
        orchestrator_url = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8080")
        try:
            self._http.post(
                f"{orchestrator_url}/api/memory/facts",
                json={
                    "scope_type": "global",
                    "content": json.dumps(server),
                    "tags": ["mcp_server", name],
                    "author_agent": "bootstrapper",
                },
                timeout=5.0,
            )
        except Exception:
            pass  # non-critical

        logger.info("Registered MCP server: %s (%s)", name, description)
        return f"OK: MCP server '{name}' registered. Workers can now use it."


# ---------------------------------------------------------------------------
# Agent handler
# ---------------------------------------------------------------------------

async def handle_task(
    task_id: str,
    message: Message,
    metadata: dict[str, Any],
) -> A2AResult:
    """Process a bootstrapper task: analyze spec and set up workspace."""
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

    logger.info("Analyzing workspace requirements for task %s", task_id)

    # Load versioned prompt (A/B selection from prompt_configs table)
    system_prompt, prompt_version, prompt_params = await load_prompt_versioned("bootstrapper")
    logger.info("Bootstrapper using prompt version=%s for task %s", prompt_version, task_id)

    executor = BootstrapExecutor()
    llm = LLMClient(agent_id="bootstrapper", prompt_version=prompt_version)

    try:
        user_msg = (
            f"Analyze the following specification and determine workspace "
            f"capabilities needed:\n\n{payload_text}"
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]

        # Use tool-calling loop — the LLM will decide whether to use tools
        # or just respond with JSON directly
        final_msg = await llm.chat_tools(
            messages=messages,
            tools=BOOTSTRAPPER_TOOLS,
            execute_tool=executor.execute,
            task_id=task_id,
            max_turns=10,
        )

        # Extract the final JSON from the LLM's text response
        result_text = final_msg.get("content", "")

        # Try to parse as JSON
        try:
            from finit_agent.llm import _strip_think_tags, _extract_json_text
            clean = _strip_think_tags(result_text)
            json_text = _extract_json_text(clean)
            capabilities = json.loads(json_text)
        except (json.JSONDecodeError, ValueError):
            # Fallback: ask again for clean JSON
            capabilities = await llm.chat_json(messages, task_id=task_id)

        # Ensure structure
        if "workspace_id" not in capabilities:
            capabilities["workspace_id"] = f"ws-{task_id[:12]}"
        if "status" not in capabilities:
            capabilities["status"] = "ready"
        if "capabilities" not in capabilities:
            capabilities["capabilities"] = {}

        # Append MCP servers if any were registered
        if executor.mcp_servers:
            capabilities["mcp_servers"] = executor.mcp_servers

        # Append installed packages info
        if executor.installed_packages:
            caps = capabilities.get("capabilities", {})
            existing_deps = caps.get("dependencies", [])
            for pkg in executor.installed_packages:
                manager, name = pkg.split(":", 1)
                existing_deps.append({"name": name, "version": "latest", "manager": manager})
            caps["dependencies"] = existing_deps
            capabilities["capabilities"] = caps

        logger.info(
            "Workspace ready for task %s: lang=%s, tools=%d, mcp_servers=%d",
            task_id,
            capabilities.get("capabilities", {}).get("runtime", {}).get("language", "?"),
            len(executor.installed_packages),
            len(executor.mcp_servers),
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
            status=TaskStatus.fail(f"Workspace analysis failed: {exc}"),
            artifacts=[],
        )
    finally:
        await llm.close()
