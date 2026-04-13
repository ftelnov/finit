"""Shared fixtures for integration tests."""
import os
import httpx
import pytest


ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8080")
LLM_ROUTER_URL = os.environ.get("LLM_ROUTER_URL", "http://localhost:8081")
MOCK_LLM_URL = os.environ.get("MOCK_LLM_URL", "http://localhost:8000")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgres://finit:finit-test-password@localhost:5432/finit")


@pytest.fixture(scope="session")
def orchestrator_url():
    return ORCHESTRATOR_URL


@pytest.fixture(scope="session")
def router_url():
    return LLM_ROUTER_URL


@pytest.fixture(scope="session")
def mock_llm_url():
    return MOCK_LLM_URL


@pytest.fixture(scope="session")
def db_url():
    return DATABASE_URL


@pytest.fixture(scope="session")
def client():
    """Shared httpx client with extended timeout for LLM calls."""
    with httpx.Client(timeout=30.0) as c:
        yield c


@pytest.fixture(scope="session")
def async_client():
    """Async httpx client."""
    return httpx.AsyncClient(timeout=30.0)
