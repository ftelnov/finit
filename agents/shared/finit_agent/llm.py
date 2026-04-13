"""LLM client for Finit agents.

Calls the LLM Router using the OpenAI-compatible chat completions API.
Adds X-Task-ID and X-Agent-ID headers for budget tracking and observability.

Supports:
- Single-shot chat (text / JSON)
- Multi-turn tool-use loops (agentic)
- Streaming
- Prompt version routing (A/B testing via weighted selection)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import os
import random
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger(__name__)

DEFAULT_LLM_ROUTER_URL = "http://mock-llm:8000"
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "mock-llm")
DEFAULT_TIMEOUT = 120.0
MAX_TOOL_TURNS = 15

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

_PROMPTS_DIR = Path(os.environ.get("PROMPTS_DIR", "/app/prompts"))


@dataclass
class PromptVersion:
    """A prompt version with its weight and optional LLM parameters."""
    version: str
    weight: int
    template_path: str
    parameters: dict[str, Any]


# Cache of prompt configs fetched from the database
_prompt_configs_cache: dict[str, list[PromptVersion]] = {}


async def fetch_prompt_configs(agent_id: str) -> list[PromptVersion]:
    """Fetch active prompt versions from the orchestrator's database.

    Falls back to default v1 if database is unavailable.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return []

    try:
        # Use asyncpg if available, otherwise fall back
        import asyncpg  # type: ignore[import-untyped]
        conn = await asyncpg.connect(db_url)
        try:
            rows = await conn.fetch(
                "SELECT version, weight, template_path, parameters "
                "FROM prompt_configs WHERE agent_id = $1 AND active = TRUE "
                "ORDER BY weight DESC",
                agent_id,
            )
            configs = []
            for row in rows:
                params = row["parameters"]
                if isinstance(params, str):
                    params = json.loads(params)
                configs.append(PromptVersion(
                    version=row["version"],
                    weight=row["weight"],
                    template_path=row["template_path"],
                    parameters=params or {},
                ))
            return configs
        finally:
            await conn.close()
    except Exception as exc:
        logger.debug("Could not fetch prompt configs for %s: %s", agent_id, exc)
        return []


def _select_version_weighted(configs: list[PromptVersion]) -> PromptVersion:
    """Select a prompt version using weighted random sampling."""
    total = sum(c.weight for c in configs)
    r = random.randint(1, total)
    cumulative = 0
    for config in configs:
        cumulative += config.weight
        if r <= cumulative:
            return config
    return configs[-1]  # fallback


async def load_prompt_versioned(agent: str) -> tuple[str, str, dict[str, Any]]:
    """Load a system prompt with A/B version routing.

    Returns (prompt_content, version, parameters).
    Checks prompt_configs table for weighted version selection, falls back to v1.
    """
    # Try fetching configs from DB (cached per-agent)
    if agent not in _prompt_configs_cache:
        configs = await fetch_prompt_configs(agent)
        if configs:
            _prompt_configs_cache[agent] = configs

    configs = _prompt_configs_cache.get(agent)
    if configs and len(configs) > 0:
        selected = _select_version_weighted(configs)
        content = load_prompt(agent, selected.version)
        if content:
            logger.info(
                "Prompt version selected: agent=%s version=%s (weight=%d)",
                agent, selected.version, selected.weight,
            )
            return content, selected.version, selected.parameters
        # Fall through to default if file doesn't exist

    # Default: v1, no extra parameters
    return load_prompt(agent, "v1"), "v1", {}


def load_prompt(agent: str, version: str = "v1") -> str:
    """Load a system prompt from file, with fallback to empty string."""
    path = _PROMPTS_DIR / agent / version / "system.md"
    if path.exists():
        return path.read_text().strip()
    # Try relative to cwd (for local dev)
    local = Path("prompts") / agent / version / "system.md"
    if local.exists():
        return local.read_text().strip()
    logger.warning("Prompt file not found: %s", path)
    return ""


def _strip_think_tags(content: str) -> str:
    """Remove <think>...</think> reasoning blocks from model output."""
    return _THINK_RE.sub("", content).strip()


def _extract_json_text(content: str) -> str:
    """Extract JSON from LLM response, handling <think> tags and code blocks."""
    text = _strip_think_tags(content)
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text


class LLMClient:
    """Client for the LLM Router (OpenAI-compatible API)."""

    def __init__(
        self,
        agent_id: str,
        router_url: str | None = None,
        default_model: str = DEFAULT_MODEL,
        prompt_version: str | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.router_url = (router_url or os.environ.get("LLM_ROUTER_URL", DEFAULT_LLM_ROUTER_URL)).rstrip("/")
        self.default_model = default_model
        self.prompt_version = prompt_version
        self._client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)

    async def close(self) -> None:
        await self._client.aclose()

    def _headers(self, task_id: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "X-Agent-ID": self.agent_id,
            "Authorization": f"Bearer {os.environ.get('JWT_SECRET', 'finit-agent-token')}",
        }
        if task_id:
            headers["X-Task-ID"] = task_id
        if self.prompt_version:
            headers["X-Prompt-Version"] = self.prompt_version
        return headers

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "stream": stream,
        }
        for key in ("temperature", "max_tokens", "top_p", "stop",
                     "response_format", "tools", "tool_choice"):
            if key in kwargs:
                payload[key] = kwargs[key]
        return payload

    # -----------------------------------------------------------------
    # Single-shot chat
    # -----------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        task_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send a non-streaming chat completion request. Returns full response dict."""
        payload = self._build_payload(messages, model=model, stream=False, **kwargs)
        url = f"{self.router_url}/v1/chat/completions"
        headers = self._headers(task_id=task_id)

        logger.debug("LLM chat request to %s model=%s", url, payload.get("model"))

        resp = await self._client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        logger.debug("LLM chat response: tokens=%s", data.get("usage", {}).get("total_tokens", "?"))
        return data

    async def chat_content(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        task_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Send a chat request and return just the assistant message content."""
        data = await self.chat(messages, model=model, task_id=task_id, **kwargs)
        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "") or ""
        return ""

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        task_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Send a chat request and parse the response content as JSON.

        Automatically requests json_object response format.
        """
        kwargs.setdefault("response_format", {"type": "json_object"})
        content = await self.chat_content(messages, model=model, task_id=task_id, **kwargs)
        text = _extract_json_text(content)
        return json.loads(text)

    # -----------------------------------------------------------------
    # Tool-use agentic loop
    # -----------------------------------------------------------------

    async def chat_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        execute_tool: Any,  # async (name, args) -> str
        model: str | None = None,
        task_id: str | None = None,
        max_turns: int = MAX_TOOL_TURNS,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Multi-turn tool-use loop.

        Calls the LLM with tools, executes any tool_calls via `execute_tool`,
        feeds results back, and repeats until the model stops calling tools
        or max_turns is reached.

        Args:
            messages: Initial message history (mutated in place).
            tools: OpenAI-format tool definitions.
            execute_tool: Async callable(name: str, args: dict) -> str.
            max_turns: Safety limit on LLM round-trips.

        Returns:
            The final assistant message dict.
        """
        for turn in range(max_turns):
            data = await self.chat(
                messages, model=model, task_id=task_id,
                tools=tools, **kwargs,
            )
            msg = data["choices"][0]["message"]

            # Normalize: ensure content key exists
            if "content" not in msg:
                msg["content"] = None

            messages.append(msg)

            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                # Model is done — no more tool calls
                logger.info("Tool loop finished after %d turns", turn + 1)
                return msg

            # Execute each tool call and append results
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = json.loads(tc["function"]["arguments"])
                logger.info("Tool call [turn %d]: %s(%s)", turn, fn_name, list(fn_args.keys()))

                try:
                    result = await execute_tool(fn_name, fn_args)
                except Exception as exc:
                    result = f"ERROR: {exc}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": str(result),
                })

        logger.warning("Tool loop hit max_turns=%d", max_turns)
        # Return the last assistant message
        return msg

    # -----------------------------------------------------------------
    # Streaming
    # -----------------------------------------------------------------

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        task_id: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Send a streaming chat completion request. Yields parsed SSE chunks."""
        payload = self._build_payload(messages, model=model, stream=True, **kwargs)
        url = f"{self.router_url}/v1/chat/completions"
        headers = self._headers(task_id=task_id)

        async with self._client.stream("POST", url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        return
                    try:
                        yield json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.warning("Failed to parse SSE chunk: %s", data_str)
                        continue
