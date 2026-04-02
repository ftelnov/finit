"""LLM client for Finit agents.

Calls the LLM Router using the OpenAI-compatible chat completions API.
Adds X-Task-ID and X-Agent-ID headers for budget tracking and observability.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger(__name__)

DEFAULT_LLM_ROUTER_URL = "http://mock-llm:8000"
DEFAULT_MODEL = "mock-llm"
DEFAULT_TIMEOUT = 120.0


class LLMClient:
    """Client for the LLM Router (OpenAI-compatible API)."""

    def __init__(
        self,
        agent_id: str,
        router_url: str | None = None,
        default_model: str = DEFAULT_MODEL,
    ) -> None:
        self.agent_id = agent_id
        self.router_url = (router_url or os.environ.get("LLM_ROUTER_URL", DEFAULT_LLM_ROUTER_URL)).rstrip("/")
        self.default_model = default_model
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
        return headers

    def _build_payload(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "stream": stream,
        }
        # Forward supported kwargs
        for key in ("temperature", "max_tokens", "top_p", "stop", "response_format"):
            if key in kwargs:
                payload[key] = kwargs[key]
        return payload

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        task_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send a non-streaming chat completion request.

        Returns the full response dict (OpenAI format).
        """
        payload = self._build_payload(messages, model=model, stream=False, **kwargs)
        url = f"{self.router_url}/v1/chat/completions"
        headers = self._headers(task_id=task_id)

        logger.debug("LLM chat request to %s model=%s", url, payload.get("model"))

        resp = await self._client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        logger.debug(
            "LLM chat response: tokens=%s",
            data.get("usage", {}).get("total_tokens", "?"),
        )
        return data

    async def chat_content(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        task_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Send a chat request and return just the assistant message content."""
        data = await self.chat(messages, model=model, task_id=task_id, **kwargs)
        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
        return ""

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        task_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Send a chat request and parse the response content as JSON."""
        content = await self.chat_content(messages, model=model, task_id=task_id, **kwargs)
        # Try to extract JSON from the response (handle markdown code blocks)
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (code block markers)
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return json.loads(text)

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        task_id: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Send a streaming chat completion request.

        Yields parsed SSE data chunks (OpenAI streaming format).
        """
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
