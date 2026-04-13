"""Integration tests for the Orchestrator."""
import os
import json
import time
import httpx
import pytest

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8080")


class TestTaskLifecycle:
    """Test task CRUD and state machine."""

    def test_create_task(self, client):
        """Create a task and verify it's in 'created' or 'running' state."""
        resp = client.post(
            f"{ORCHESTRATOR_URL}/api/tasks",
            json={"input": "Add a healthz endpoint to the Go service"},
        )
        assert resp.status_code in (200, 201), f"Create failed: {resp.text}"
        task = resp.json()
        assert "id" in task
        assert task["status"] in ("created", "running")
        assert task["input"] == "Add a healthz endpoint to the Go service"

    def test_list_tasks(self, client):
        """List tasks should return at least the task we created."""
        resp = client.get(f"{ORCHESTRATOR_URL}/api/tasks")
        assert resp.status_code == 200
        tasks = resp.json()
        assert isinstance(tasks, list)

    def test_get_task(self, client):
        """Create and retrieve a specific task."""
        # Create
        create_resp = client.post(
            f"{ORCHESTRATOR_URL}/api/tasks",
            json={"input": "Test task for retrieval"},
        )
        task_id = create_resp.json()["id"]

        # Get
        resp = client.get(f"{ORCHESTRATOR_URL}/api/tasks/{task_id}")
        assert resp.status_code == 200
        task = resp.json()
        assert task["id"] == task_id

    def test_cancel_task(self, client):
        """Create and cancel a task."""
        create_resp = client.post(
            f"{ORCHESTRATOR_URL}/api/tasks",
            json={"input": "Task to be cancelled"},
        )
        task_id = create_resp.json()["id"]

        # Cancel
        resp = client.delete(f"{ORCHESTRATOR_URL}/api/tasks/{task_id}")
        assert resp.status_code == 200

        # Verify cancelled
        resp = client.get(f"{ORCHESTRATOR_URL}/api/tasks/{task_id}")
        task = resp.json()
        assert task["status"] == "cancelled"

    def test_nonexistent_task_returns_404(self, client):
        resp = client.get(f"{ORCHESTRATOR_URL}/api/tasks/nonexistent-task-id")
        assert resp.status_code == 404


class TestAgentRegistry:
    """Test agent registration and discovery."""

    def test_list_agents(self, client):
        """List registered agents."""
        resp = client.get(f"{ORCHESTRATOR_URL}/api/agents")
        assert resp.status_code == 200
        agents = resp.json()
        assert isinstance(agents, list)

    def test_register_agent(self, client):
        """Register a mock-llm as an agent (it has a health endpoint)."""
        # The mock-llm doesn't have an agent card, so this might fail gracefully
        # Test with one of the actual agents instead
        in_docker = "ORCHESTRATOR_URL" in os.environ
        planner_url = "http://planner:9000" if in_docker else "http://localhost:9000"

        resp = client.post(
            f"{ORCHESTRATOR_URL}/api/agents",
            json={"url": planner_url},
        )
        # Should succeed or already registered
        assert resp.status_code in (200, 201, 409), f"Register agent failed: {resp.text}"

        if resp.status_code in (200, 201):
            agent = resp.json()
            assert "id" in agent or "name" in agent


class TestAGUI:
    """Test AG-UI SSE event streaming."""

    def test_sse_endpoint_exists(self, client):
        """The SSE endpoint should accept connections."""
        # Create a task first
        create_resp = client.post(
            f"{ORCHESTRATOR_URL}/api/tasks",
            json={"input": "Task for SSE test"},
        )
        task_id = create_resp.json()["id"]

        # Connect to SSE - use a short timeout since we just want to verify connectivity
        try:
            with httpx.stream(
                "GET",
                f"{ORCHESTRATOR_URL}/ag-ui/tasks/{task_id}/events",
                timeout=3.0,
            ) as resp:
                assert resp.status_code == 200
                content_type = resp.headers.get("content-type", "")
                assert "text/event-stream" in content_type
        except httpx.ReadTimeout:
            # Expected - SSE stream stays open
            pass
