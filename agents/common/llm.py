"""LLM client configured for z.ai / GLM-5 via OpenAI-compatible API."""

import os
import json
from openai import OpenAI


def get_llm_client() -> OpenAI:
    """Create an OpenAI-compatible client pointed at z.ai."""
    api_key = os.getenv("ZAI_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
    return OpenAI(api_key=api_key, base_url=base_url)


def get_model() -> str:
    return os.getenv("LLM_MODEL", "glm-5")


def chat_json(
    client: OpenAI,
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    temperature: float = 0.3,
) -> dict:
    """Send a chat completion request and parse JSON response."""
    model = model or get_model()

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content
    return json.loads(content)


def chat_text(
    client: OpenAI,
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    temperature: float = 0.3,
) -> str:
    """Send a chat completion and return text response."""
    model = model or get_model()

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
    )

    return response.choices[0].message.content
