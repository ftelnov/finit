"""Integration tests for the LLM Router (Pingora-based gateway)."""
import os
import json
import httpx
import pytest

LLM_ROUTER_URL = os.environ.get("LLM_ROUTER_URL", "http://localhost:8081")


class TestChatCompletions:
    """Test the core proxy functionality: /v1/chat/completions."""

    def test_basic_chat_completion(self, client):
        """Non-streaming chat completion through the router to mock-llm."""
        resp = client.post(
            f"{LLM_ROUTER_URL}/v1/chat/completions",
            json={
                "model": "mock-llm",
                "messages": [{"role": "user", "content": "Hello, world!"}],
                "max_tokens": 100,
            },
            headers={
                "Authorization": "Bearer test-token",
                "X-Task-ID": "test-task-1",
                "X-Agent-ID": "test",
            },
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "choices" in data
        assert len(data["choices"]) > 0
        assert "message" in data["choices"][0]
        assert "content" in data["choices"][0]["message"]
        assert "usage" in data

    def test_streaming_chat_completion(self, client):
        """Streaming (SSE) chat completion through the router."""
        resp = client.post(
            f"{LLM_ROUTER_URL}/v1/chat/completions",
            json={
                "model": "mock-llm",
                "messages": [{"role": "user", "content": "Hello streaming!"}],
                "stream": True,
                "max_tokens": 50,
            },
            headers={
                "Authorization": "Bearer test-token",
                "X-Task-ID": "test-task-2",
                "X-Agent-ID": "test",
            },
        )
        assert resp.status_code == 200
        # Should be SSE content type
        assert "text/event-stream" in resp.headers.get("content-type", "")

        # Parse SSE chunks
        chunks = []
        for line in resp.text.strip().split("\n"):
            if line.startswith("data: "):
                data = line[6:]
                if data == "[DONE]":
                    break
                chunks.append(json.loads(data))

        assert len(chunks) > 0, "Expected at least one SSE chunk"

    def test_missing_model_returns_400(self, client):
        """Request without model field should return 400."""
        resp = client.post(
            f"{LLM_ROUTER_URL}/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "no model"}]},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 400
        assert "model" in resp.json()["error"]["message"].lower()

    def test_missing_auth_returns_401(self, client):
        """Request without authorization should return 401."""
        resp = client.post(
            f"{LLM_ROUTER_URL}/v1/chat/completions",
            json={
                "model": "mock-llm",
                "messages": [{"role": "user", "content": "no auth"}],
            },
        )
        assert resp.status_code == 401

    def test_invalid_json_returns_400(self, client):
        """Malformed JSON body should return 400."""
        resp = client.post(
            f"{LLM_ROUTER_URL}/v1/chat/completions",
            content=b"not json",
            headers={
                "Authorization": "Bearer test-token",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 400

    def test_unknown_model_returns_503(self, client):
        """Request for a model no provider serves should return 503."""
        resp = client.post(
            f"{LLM_ROUTER_URL}/v1/chat/completions",
            json={
                "model": "nonexistent-model-xyz",
                "messages": [{"role": "user", "content": "hello"}],
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 503


class TestProviderManagement:
    """Test the provider management API."""

    def test_list_providers(self, client):
        """GET /v1/providers should return the configured providers."""
        resp = client.get(f"{LLM_ROUTER_URL}/v1/providers")
        assert resp.status_code == 200
        data = resp.json()
        providers = data.get("providers", data) if isinstance(data, dict) else data
        assert isinstance(providers, list)
        # Should have at least the mock provider from config
        assert len(providers) >= 1
        mock = next((p for p in providers if p.get("name") == "mock"), None)
        assert mock is not None, f"Mock provider not found in {providers}"

    def test_register_and_delete_provider(self, client):
        """Register a new provider, verify it appears, then delete it."""
        new_provider = {
            "name": "test-provider",
            "url": "http://mock-llm:8000/v1",
            "models": {"test-model": {"pricing": {"input_per_1m": 0.0, "output_per_1m": 0.0}}},
            "weight": 1,
        }
        # Register
        resp = client.post(
            f"{LLM_ROUTER_URL}/v1/providers",
            json=new_provider,
            headers={"Authorization": "Bearer admin-token"},
        )
        assert resp.status_code in (200, 201), f"Register failed: {resp.text}"

        # Verify it's listed
        resp = client.get(f"{LLM_ROUTER_URL}/v1/providers")
        data = resp.json()
        providers = data.get("providers", data) if isinstance(data, dict) else data
        names = [p.get("name", "") for p in providers]
        assert "test-provider" in names

        # Delete
        resp = client.delete(
            f"{LLM_ROUTER_URL}/v1/providers/test-provider",
            headers={"Authorization": "Bearer admin-token"},
        )
        assert resp.status_code in (200, 204)

        # Verify it's gone
        resp = client.get(f"{LLM_ROUTER_URL}/v1/providers")
        data = resp.json()
        providers = data.get("providers", data) if isinstance(data, dict) else data
        names = [p.get("name", "") for p in providers]
        assert "test-provider" not in names


class TestUsage:
    """Test usage tracking."""

    def test_usage_endpoint(self, client):
        resp = client.get(f"{LLM_ROUTER_URL}/v1/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_requests" in data or isinstance(data, dict)
