"""Test that all services are healthy and reachable."""
import os
import httpx
import pytest

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8080")
LLM_ROUTER_URL = os.environ.get("LLM_ROUTER_URL", "http://localhost:8081")
MOCK_LLM_URL = os.environ.get("MOCK_LLM_URL", "http://localhost:8000")

AGENT_URLS = {
    "planner": os.environ.get("PLANNER_URL", "http://planner:9000" if "ORCHESTRATOR_URL" in os.environ else "http://localhost:9000"),
    "bootstrapper": os.environ.get("BOOTSTRAPPER_URL", "http://bootstrapper:9001" if "ORCHESTRATOR_URL" in os.environ else "http://localhost:9001"),
    "worker": os.environ.get("WORKER_URL", "http://worker:9002" if "ORCHESTRATOR_URL" in os.environ else "http://localhost:9002"),
    "reviewer": os.environ.get("REVIEWER_URL", "http://reviewer:9003" if "ORCHESTRATOR_URL" in os.environ else "http://localhost:9003"),
}


class TestServiceHealth:
    """Verify all services are up and healthy."""

    def test_orchestrator_health(self, client):
        resp = client.get(f"{ORCHESTRATOR_URL}/health")
        assert resp.status_code == 200

    def test_llm_router_health(self, client):
        resp = client.get(f"{LLM_ROUTER_URL}/health")
        assert resp.status_code == 200

    def test_mock_llm_health(self, client):
        resp = client.get(f"{MOCK_LLM_URL}/health")
        assert resp.status_code == 200

    @pytest.mark.parametrize("agent_name", ["planner", "bootstrapper", "worker", "reviewer"])
    def test_agent_health(self, client, agent_name):
        url = AGENT_URLS[agent_name]
        resp = client.get(f"{url}/health")
        assert resp.status_code == 200

    @pytest.mark.parametrize("agent_name", ["planner", "bootstrapper", "worker", "reviewer"])
    def test_agent_card(self, client, agent_name):
        url = AGENT_URLS[agent_name]
        resp = client.get(f"{url}/.well-known/agent.json")
        assert resp.status_code == 200
        card = resp.json()
        assert card["name"] == agent_name
        assert "skills" in card
        assert "capabilities" in card


class TestMetrics:
    """Verify Prometheus metrics endpoints."""

    def test_orchestrator_metrics(self, client):
        resp = client.get(f"{ORCHESTRATOR_URL}/metrics")
        assert resp.status_code == 200
        assert "finit_" in resp.text or "# HELP" in resp.text

    def test_llm_router_metrics(self, client):
        resp = client.get(f"{LLM_ROUTER_URL}/metrics")
        assert resp.status_code == 200
