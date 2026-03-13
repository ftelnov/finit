"""E2E test fixtures."""

import os
import time
import pytest
import requests


ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8080")


@pytest.fixture(scope="session")
def api_url():
    """Return the orchestrator API base URL, waiting for it to be ready."""
    url = ORCHESTRATOR_URL
    deadline = time.time() + 60  # 60s timeout for services to come up

    while time.time() < deadline:
        try:
            r = requests.get(f"{url}/health", timeout=3)
            if r.status_code == 200:
                return url
        except requests.ConnectionError:
            pass
        time.sleep(2)

    pytest.fail(f"Orchestrator at {url} not ready after 60s")
