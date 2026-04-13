"""Integration tests for A2A agent protocol compliance."""
import os
import json
import httpx
import pytest

AGENT_URLS = {}
if "ORCHESTRATOR_URL" in os.environ:
    # Running inside Docker network
    AGENT_URLS = {
        "planner": "http://planner:9000",
        "bootstrapper": "http://bootstrapper:9001",
        "worker": "http://worker:9002",
        "reviewer": "http://reviewer:9003",
    }
else:
    AGENT_URLS = {
        "planner": "http://localhost:9000",
        "bootstrapper": "http://localhost:9001",
        "worker": "http://localhost:9002",
        "reviewer": "http://localhost:9003",
    }


def make_a2a_request(task_id: str, message_text: str, metadata: dict = None):
    """Build a valid A2A JSON-RPC 2.0 request."""
    return {
        "jsonrpc": "2.0",
        "method": "tasks/send",
        "params": {
            "id": task_id,
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": message_text}],
            },
            "metadata": metadata or {},
        },
        "id": "test-req-1",
    }


class TestA2AProtocol:
    """Test A2A JSON-RPC 2.0 protocol compliance for all agents."""

    @pytest.mark.parametrize("agent_name", ["planner", "bootstrapper", "worker", "reviewer"])
    def test_agent_card_schema(self, client, agent_name):
        """Agent card must have required A2A fields."""
        url = AGENT_URLS[agent_name]
        resp = client.get(f"{url}/.well-known/agent.json")
        assert resp.status_code == 200
        card = resp.json()

        # Required A2A fields
        assert "name" in card
        assert "url" in card
        assert "version" in card
        assert "capabilities" in card
        assert "skills" in card
        assert isinstance(card["skills"], list)
        assert len(card["skills"]) > 0

    @pytest.mark.parametrize("agent_name", ["planner", "bootstrapper", "worker", "reviewer"])
    def test_invalid_method_returns_error(self, client, agent_name):
        """Unknown JSON-RPC method should return error -32601."""
        url = AGENT_URLS[agent_name]
        resp = client.post(
            url,
            json={
                "jsonrpc": "2.0",
                "method": "unknown/method",
                "params": {},
                "id": "test-err-1",
            },
        )
        assert resp.status_code == 200  # JSON-RPC errors are still 200
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32601


class TestPlannerAgent:
    """Test the planner agent's task processing."""

    def test_planner_generates_spec(self, client):
        """Send a task to planner, expect a structured spec back."""
        url = AGENT_URLS["planner"]
        payload = json.dumps({
            "task_description": "Add a /healthz endpoint that returns 200 OK with uptime",
            "project_context": {"language": "go", "framework": "chi"},
        })

        resp = client.post(
            url,
            json=make_a2a_request("test-plan-1", payload),
            timeout=30.0,
        )
        assert resp.status_code == 200
        data = resp.json()

        assert "result" in data, f"Expected result, got: {data}"
        result = data["result"]
        assert result["status"]["state"] == "completed"
        assert len(result.get("artifacts", [])) > 0

        # Parse the artifact text as JSON (should be a spec)
        artifact_text = result["artifacts"][0]["parts"][0]["text"]
        spec = json.loads(artifact_text)
        assert "title" in spec or "description" in spec


class TestBootstrapperAgent:
    """Test the bootstrapper agent."""

    def test_bootstrapper_returns_capabilities(self, client):
        """Send a spec to bootstrapper, expect workspace capabilities back."""
        url = AGENT_URLS["bootstrapper"]
        payload = json.dumps({
            "spec": {
                "title": "Add healthz endpoint",
                "description": "Add GET /healthz",
                "domains": ["go-backend"],
            },
            "project": {"language": "go", "version": "1.22"},
        })

        resp = client.post(
            url,
            json=make_a2a_request("test-boot-1", payload),
            timeout=30.0,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data
        assert data["result"]["status"]["state"] == "completed"


class TestWorkerAgent:
    """Test the worker agent."""

    def test_worker_generates_artifacts(self, client):
        """Send a task to worker, expect code artifacts back."""
        url = AGENT_URLS["worker"]
        payload = json.dumps({
            "spec": {
                "title": "Add healthz endpoint",
                "acceptance_criteria": ["GET /healthz returns 200"],
            },
            "workspace": {
                "runtime": {"language": "go", "version": "1.22"},
                "tools": [{"name": "go", "version": "1.22"}],
            },
        })

        resp = client.post(
            url,
            json=make_a2a_request("test-work-1", payload),
            timeout=30.0,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data
        assert data["result"]["status"]["state"] == "completed"


class TestReviewerAgent:
    """Test the reviewer agent."""

    def test_reviewer_returns_verdict(self, client):
        """Send artifacts to reviewer, expect a review verdict."""
        url = AGENT_URLS["reviewer"]
        payload = json.dumps({
            "spec": {
                "title": "Add healthz endpoint",
                "acceptance_criteria": ["GET /healthz returns 200"],
            },
            "artifacts": {
                "files_changed": ["handler.go"],
                "diff": "+func Healthz(w http.ResponseWriter, r *http.Request) {...}",
            },
            "test_results": {"exit_code": 0, "stdout": "PASS"},
        })

        resp = client.post(
            url,
            json=make_a2a_request("test-review-1", payload),
            timeout=30.0,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data
        assert data["result"]["status"]["state"] == "completed"

        # Parse verdict
        artifact_text = data["result"]["artifacts"][0]["parts"][0]["text"]
        review = json.loads(artifact_text)
        assert "verdict" in review
        assert review["verdict"] in ("PASS", "FAIL")
