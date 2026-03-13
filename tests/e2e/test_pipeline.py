"""E2E tests for the Finit agent pipeline."""

import time
import pytest
import requests


class TestHealthCheck:
    """Basic connectivity tests."""

    def test_orchestrator_health(self, api_url):
        r = requests.get(f"{api_url}/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"

    def test_list_tasks_empty(self, api_url):
        r = requests.get(f"{api_url}/api/tasks")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestTaskCreation:
    """Test task submission."""

    def test_create_task(self, api_url):
        r = requests.post(
            f"{api_url}/api/tasks",
            json={"input": "Add a /healthz endpoint to the Go service"},
        )
        assert r.status_code == 201
        data = r.json()
        assert "id" in data
        assert data["status"] == "created"
        assert data["input"] == "Add a /healthz endpoint to the Go service"

    def test_create_task_empty_input(self, api_url):
        r = requests.post(f"{api_url}/api/tasks", json={"input": ""})
        assert r.status_code == 400

    def test_get_task_not_found(self, api_url):
        r = requests.get(f"{api_url}/api/task", params={"id": "nonexistent"})
        assert r.status_code == 404


class TestFullPipeline:
    """End-to-end pipeline test: submit task and wait for completion."""

    @pytest.mark.timeout(180)
    def test_task_flows_through_pipeline(self, api_url):
        """Submit a task and verify it flows through all agents to completion."""
        # Create task.
        r = requests.post(
            f"{api_url}/api/tasks",
            json={"input": "Create a simple Python function that calculates fibonacci numbers"},
        )
        assert r.status_code == 201
        task_id = r.json()["id"]

        # Poll until completed or timeout.
        deadline = time.time() + 150  # 150s for full pipeline
        final_task = None

        while time.time() < deadline:
            r = requests.get(f"{api_url}/api/task", params={"id": task_id})
            assert r.status_code == 200
            task = r.json()

            print(f"  Task {task_id} status: {task['status']}")

            if task["status"] == "completed":
                final_task = task
                break

            time.sleep(3)

        assert final_task is not None, f"Task did not complete within timeout. Last status: {task['status']}"
        assert final_task["status"] == "completed"

        # Verify all pipeline stages produced output.
        assert final_task.get("domains") is not None, "Missing domains from router"
        assert len(final_task["domains"]) > 0, "No domains classified"

        assert final_task.get("spec") is not None, "Missing spec"

        assert final_task.get("code") is not None, "Missing code"

        assert final_task.get("review") is not None, "Missing review"

        # Verify review structure.
        review = final_task["review"]
        if isinstance(review, str):
            import json
            review = json.loads(review)

        assert "verdict" in review, "Review missing verdict"
        assert review["verdict"] in ("PASS", "FAIL"), f"Invalid verdict: {review['verdict']}"
        assert "score" in review, "Review missing score"
        assert "findings" in review, "Review missing findings"

        print(f"\n  Pipeline completed!")
        print(f"  Domains: {final_task['domains']}")
        print(f"  Review verdict: {review['verdict']}")
        print(f"  Review score: {review['score']}")


class TestMultipleTasks:
    """Test handling multiple concurrent tasks."""

    @pytest.mark.timeout(300)
    def test_two_tasks_complete(self, api_url):
        """Submit two tasks and verify both complete."""
        tasks = []

        for desc in [
            "Write a Go function that reverses a string",
            "Create a Python script that reads a CSV file",
        ]:
            r = requests.post(f"{api_url}/api/tasks", json={"input": desc})
            assert r.status_code == 201
            tasks.append(r.json()["id"])

        deadline = time.time() + 240
        completed = set()

        while time.time() < deadline and len(completed) < len(tasks):
            for tid in tasks:
                if tid in completed:
                    continue
                r = requests.get(f"{api_url}/api/task", params={"id": tid})
                task = r.json()
                if task["status"] == "completed":
                    completed.add(tid)
                    print(f"  Task {tid} completed")
            time.sleep(3)

        assert len(completed) == len(tasks), (
            f"Only {len(completed)}/{len(tasks)} tasks completed"
        )
