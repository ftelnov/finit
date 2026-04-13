"""End-to-end integration test: full happy path through the system."""
import os
import json
import time
import httpx
import pytest

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8080")
LLM_ROUTER_URL = os.environ.get("LLM_ROUTER_URL", "http://localhost:8081")

AGENT_URLS = {}
if "ORCHESTRATOR_URL" in os.environ:
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


class TestE2ELLMRouterToMockProvider:
    """E2E: Client → LLM Router → Mock LLM → Client."""

    def test_full_roundtrip_non_streaming(self, client):
        """Complete non-streaming request through the entire proxy pipeline."""
        resp = client.post(
            f"{LLM_ROUTER_URL}/v1/chat/completions",
            json={
                "model": "mock-llm",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "What is 2+2?"},
                ],
                "max_tokens": 100,
                "temperature": 0.0,
            },
            headers={
                "Authorization": "Bearer test-token",
                "X-Task-ID": "e2e-test-1",
                "X-Agent-ID": "e2e-test",
            },
        )
        assert resp.status_code == 200
        data = resp.json()

        # Full OpenAI response structure
        assert "id" in data
        assert "choices" in data
        assert len(data["choices"]) == 1
        assert "message" in data["choices"][0]
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert len(data["choices"][0]["message"]["content"]) > 0
        assert "usage" in data
        assert data["usage"]["prompt_tokens"] > 0
        assert data["usage"]["completion_tokens"] > 0
        assert data["usage"]["total_tokens"] == data["usage"]["prompt_tokens"] + data["usage"]["completion_tokens"]

    def test_full_roundtrip_streaming(self, client):
        """Complete streaming request through the proxy pipeline."""
        resp = client.post(
            f"{LLM_ROUTER_URL}/v1/chat/completions",
            json={
                "model": "mock-llm",
                "messages": [{"role": "user", "content": "Count to 3"}],
                "stream": True,
                "max_tokens": 50,
            },
            headers={
                "Authorization": "Bearer test-token",
                "X-Task-ID": "e2e-test-2",
                "X-Agent-ID": "e2e-test",
            },
        )
        assert resp.status_code == 200

        # Parse all SSE chunks
        content_parts = []
        has_done = False
        for line in resp.text.strip().split("\n"):
            if line.startswith("data: "):
                data_str = line[6:]
                if data_str == "[DONE]":
                    has_done = True
                    continue
                chunk = json.loads(data_str)
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    delta = chunk["choices"][0].get("delta", {})
                    if "content" in delta:
                        content_parts.append(delta["content"])

        assert len(content_parts) > 0, "Expected streaming content chunks"
        full_content = "".join(content_parts)
        assert len(full_content) > 0


class TestE2EAgentPipeline:
    """E2E: Test the full agent pipeline through direct A2A calls."""

    def test_planner_to_reviewer_pipeline(self, client):
        """Simulate the orchestrator's pipeline: planner → bootstrapper → worker → reviewer."""

        # Step 1: Planner generates spec
        plan_resp = client.post(
            AGENT_URLS["planner"],
            json={
                "jsonrpc": "2.0",
                "method": "tasks/send",
                "params": {
                    "id": "e2e-pipeline-1",
                    "message": {
                        "role": "user",
                        "parts": [{
                            "type": "text",
                            "text": json.dumps({
                                "task_description": "Add a /healthz endpoint returning JSON {status: ok, uptime: N}",
                                "project_context": {"language": "go"},
                            }),
                        }],
                    },
                    "metadata": {},
                },
                "id": "plan-req",
            },
            timeout=30.0,
        )
        assert plan_resp.status_code == 200
        plan_data = plan_resp.json()
        assert plan_data["result"]["status"]["state"] == "completed"
        spec_text = plan_data["result"]["artifacts"][0]["parts"][0]["text"]
        spec = json.loads(spec_text)

        # Step 2: Bootstrapper prepares workspace
        boot_resp = client.post(
            AGENT_URLS["bootstrapper"],
            json={
                "jsonrpc": "2.0",
                "method": "tasks/send",
                "params": {
                    "id": "e2e-pipeline-1",
                    "message": {
                        "role": "user",
                        "parts": [{
                            "type": "text",
                            "text": json.dumps({
                                "spec": spec,
                                "project": {"language": "go", "version": "1.22"},
                            }),
                        }],
                    },
                    "metadata": {},
                },
                "id": "boot-req",
            },
            timeout=30.0,
        )
        assert boot_resp.status_code == 200
        boot_data = boot_resp.json()
        assert boot_data["result"]["status"]["state"] == "completed"
        workspace_text = boot_data["result"]["artifacts"][0]["parts"][0]["text"]
        workspace = json.loads(workspace_text)

        # Step 3: Worker generates code
        work_resp = client.post(
            AGENT_URLS["worker"],
            json={
                "jsonrpc": "2.0",
                "method": "tasks/send",
                "params": {
                    "id": "e2e-pipeline-1",
                    "message": {
                        "role": "user",
                        "parts": [{
                            "type": "text",
                            "text": json.dumps({
                                "spec": spec,
                                "workspace": workspace,
                            }),
                        }],
                    },
                    "metadata": {},
                },
                "id": "work-req",
            },
            timeout=30.0,
        )
        assert work_resp.status_code == 200
        work_data = work_resp.json()
        assert work_data["result"]["status"]["state"] == "completed"
        artifacts_text = work_data["result"]["artifacts"][0]["parts"][0]["text"]
        artifacts = json.loads(artifacts_text)

        # Step 4: Reviewer evaluates
        review_resp = client.post(
            AGENT_URLS["reviewer"],
            json={
                "jsonrpc": "2.0",
                "method": "tasks/send",
                "params": {
                    "id": "e2e-pipeline-1",
                    "message": {
                        "role": "user",
                        "parts": [{
                            "type": "text",
                            "text": json.dumps({
                                "spec": spec,
                                "artifacts": artifacts,
                                "test_results": {"exit_code": 0, "stdout": "PASS"},
                            }),
                        }],
                    },
                    "metadata": {},
                },
                "id": "review-req",
            },
            timeout=30.0,
        )
        assert review_resp.status_code == 200
        review_data = review_resp.json()
        assert review_data["result"]["status"]["state"] == "completed"
        review_text = review_data["result"]["artifacts"][0]["parts"][0]["text"]
        review = json.loads(review_text)
        assert review["verdict"] in ("PASS", "FAIL")


class TestE2EOrchestratorTask:
    """E2E: Submit a task to orchestrator and watch it progress."""

    def test_submit_and_track_task(self, client):
        """Submit a task and verify it reaches a terminal state."""
        # First register all agents
        for agent_name, agent_url in AGENT_URLS.items():
            client.post(
                f"{ORCHESTRATOR_URL}/api/agents",
                json={"url": agent_url},
            )

        # Create a task
        resp = client.post(
            f"{ORCHESTRATOR_URL}/api/tasks",
            json={
                "input": "Add a /healthz endpoint to the Go service",
                "project_id": "test-project",
            },
        )
        assert resp.status_code in (200, 201), f"Create task failed: {resp.text}"
        task = resp.json()
        task_id = task["id"]

        # Poll for task completion (max 60 seconds)
        terminal_states = {"completed", "failed", "escalated", "cancelled", "awaiting_input"}
        for _ in range(30):
            time.sleep(2)
            resp = client.get(f"{ORCHESTRATOR_URL}/api/tasks/{task_id}")
            assert resp.status_code == 200
            task = resp.json()
            if task["status"] in terminal_states:
                break

        # Task should have progressed beyond 'created'
        assert task["status"] != "created", \
            f"Task stuck in 'created' state after 60s. Status: {task['status']}"

        # If awaiting_input, approve the spec
        if task["status"] == "awaiting_input":
            resp = client.post(
                f"{ORCHESTRATOR_URL}/api/tasks/{task_id}/input",
                json={"action": "approve"},
            )
            assert resp.status_code == 200

            # Wait a bit more for completion
            for _ in range(30):
                time.sleep(2)
                resp = client.get(f"{ORCHESTRATOR_URL}/api/tasks/{task_id}")
                task = resp.json()
                if task["status"] in {"completed", "failed", "escalated"}:
                    break

        # Verify task reached a meaningful state
        assert task["status"] in {"completed", "failed", "escalated", "awaiting_input"}, \
            f"Task ended in unexpected state: {task['status']}"
