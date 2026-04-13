"""L9 evals: MCP server discovery and external service access.

Tests that the bootstrapper can detect external service needs,
and the worker can use clients to access them.

Requires OpenSearch running at localhost:9200 with seeded data.
Run: python seed_opensearch.py before this test.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from conftest import a2a_send, build_project_context
from dataset import EvalLevel, cases_by_level
from judge import full_judge

logger = logging.getLogger(__name__)


def opensearch_available() -> bool:
    """Check if OpenSearch is reachable and has data."""
    try:
        resp = httpx.get("http://localhost:9200/app-logs-2026.04/_count", timeout=3.0)
        return resp.status_code == 200 and resp.json().get("count", 0) > 0
    except Exception:
        return False


@pytest.mark.skipif(
    not opensearch_available(),
    reason="OpenSearch not available or not seeded (run: python seed_opensearch.py)",
)
class TestL9McpDiscovery:
    """External service discovery and usage."""

    @pytest.fixture(autouse=True)
    def _setup(self, client, make_repo):
        self.client = client
        self.make_repo = make_repo

    @pytest.mark.parametrize(
        "case",
        cases_by_level(EvalLevel.L9_MCP_DISCOVERY),
        ids=[c.id for c in cases_by_level(EvalLevel.L9_MCP_DISCOVERY)],
    )
    def test_mcp_discovery(self, case):
        repo = self.make_repo(case.repo_type)
        project_ctx = build_project_context(repo)

        # ── Planner ──
        spec = a2a_send(
            self.client, "planner", f"eval-{case.id}-plan",
            {"task_description": case.task_description, "project_context": project_ctx},
            timeout=case.timeout_s,
        )
        logger.info("Spec for %s: %s", case.id, spec.get("title", "?"))

        # ── Bootstrapper (should detect OpenSearch need) ──
        capabilities = a2a_send(
            self.client, "bootstrapper", f"eval-{case.id}-boot",
            {"spec": spec, "project": project_ctx},
            timeout=case.timeout_s,
        )
        logger.info("Capabilities for %s: %s", case.id,
                     json.dumps(capabilities, indent=2)[:500])

        # Check if bootstrapper detected opensearch need
        caps_str = json.dumps(capabilities).lower()
        opensearch_detected = "opensearch" in caps_str
        logger.info("OpenSearch detected by bootstrapper: %s", opensearch_detected)

        # Check bootstrapper's approach: MCP server vs library
        mcp_servers = capabilities.get("mcp_servers", [])
        mcp_approach = any("opensearch" in s.get("name", "").lower() for s in mcp_servers)
        deps = capabilities.get("capabilities", capabilities).get("dependencies", [])
        lib_approach = any("opensearch" in d.get("name", "").lower() for d in deps)
        logger.info(
            "Bootstrapper approach for %s: mcp=%s library=%s (mcp_servers=%d deps_with_opensearch=%d)",
            case.id, mcp_approach, lib_approach,
            len(mcp_servers),
            sum(1 for d in deps if "opensearch" in d.get("name", "").lower()),
        )

        # ── Worker ──
        worker_result = a2a_send(
            self.client, "worker", f"eval-{case.id}-work",
            {"spec": spec, "workspace": capabilities, "project": project_ctx},
            timeout=case.timeout_s,
        )

        # ── Reviewer ──
        review_result = a2a_send(
            self.client, "reviewer", f"eval-{case.id}-review",
            {"spec": spec, "artifacts": worker_result.get("artifacts", []),
             "test_results": worker_result.get("test_results", {})},
            timeout=case.timeout_s,
        )

        # ── Judge ──
        verdict = full_judge(
            client=self.client,
            case_id=case.id,
            spec=spec,
            worker_result=worker_result,
            review_result=review_result,
            capabilities=capabilities,
            expected_lang="python",
            file_pattern=r"\.py$",
            required_patterns=case.required_patterns or None,
            use_llm_judge=case.use_llm_judge,
        )

        print(f"\n{verdict.summary()}")

        if not verdict.passed:
            failed = [c for c in verdict.static_checks if not c.passed]
            detail = "; ".join(f"{c.name}" for c in failed)
            if verdict.error:
                detail = f"ERROR: {verdict.error}"
            pytest.fail(f"{case.id}: {detail} (score={verdict.overall_score:.0%})")
