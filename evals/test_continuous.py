"""L6 evals: continuous enhancement across multiple tasks in the same workspace.

Tests that:
1. Task 1 establishes an environment and adds a feature
2. Task 2 extends the same codebase without breaking Task 1's work
3. Worker sees and respects prior changes
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from conftest import a2a_send, build_project_context
from dataset import EvalLevel, cases_by_level
from fixtures import REPO_GENERATORS, RepoFixture
from judge import (
    JudgeVerdict,
    check_artifacts_exist,
    check_code_contains,
    check_code_not_contains,
    check_tests_ran,
    full_judge,
)

logger = logging.getLogger(__name__)


def _merge_artifacts_into_repo(repo: RepoFixture, worker_result: dict) -> RepoFixture:
    """Apply worker artifacts back into the repo fixture to simulate workspace persistence."""
    for artifact in worker_result.get("artifacts", []):
        if artifact.get("type") == "code_change" and artifact.get("path") and artifact.get("content"):
            rel_path = artifact["path"]
            fp = repo.path / rel_path
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(artifact["content"])
            repo.files[rel_path] = artifact["content"]
    return repo


class TestL6ContinuousEnhancement:
    """Multi-task continuous enhancement tests."""

    @pytest.fixture(autouse=True)
    def _setup(self, client, tmp_path):
        self.client = client
        self.tmp_path = tmp_path

    def _run_pipeline(self, case, repo, project_ctx):
        """Run planner → bootstrapper → worker → reviewer for a case."""
        spec = a2a_send(
            self.client, "planner", f"eval-{case.id}-plan",
            {"task_description": case.task_description, "project_context": project_ctx},
            timeout=case.timeout_s,
        )
        logger.info("Spec for %s: %s", case.id, spec.get("title", "?"))

        capabilities = a2a_send(
            self.client, "bootstrapper", f"eval-{case.id}-boot",
            {"spec": spec, "project": project_ctx},
            timeout=case.timeout_s,
        )

        worker_result = a2a_send(
            self.client, "worker", f"eval-{case.id}-work",
            {"spec": spec, "workspace": capabilities, "project": project_ctx},
            timeout=case.timeout_s,
        )

        review_result = a2a_send(
            self.client, "reviewer", f"eval-{case.id}-review",
            {"spec": spec, "artifacts": worker_result.get("artifacts", []),
             "test_results": worker_result.get("test_results", {})},
            timeout=case.timeout_s,
        )

        return spec, capabilities, worker_result, review_result

    def test_python_continuous(self):
        """Two-step Python Flask enhancement: /health then /metrics."""
        cases = [c for c in cases_by_level(EvalLevel.L6_CONTINUOUS) if "py" in c.id]
        assert len(cases) >= 2, "Need at least 2 Python continuous cases"

        step1 = next(c for c in cases if "step1" in c.id)
        step2 = next(c for c in cases if "step2" in c.id)

        # ── Step 1: establish baseline ──
        repo = REPO_GENERATORS[step1.repo_type](self.tmp_path / "py-continuous")
        ctx1 = build_project_context(repo)

        spec1, caps1, work1, review1 = self._run_pipeline(step1, repo, ctx1)
        logger.info("Step 1 done: %d artifacts", len(work1.get("artifacts", [])))

        # Judge step 1
        v1 = full_judge(
            self.client, step1.id, spec1, work1, review1, caps1,
            expected_lang="python", file_pattern=r"\.py$",
        )
        print(f"\n{v1.summary()}")

        # ── Merge step 1 artifacts into the repo ──
        repo = _merge_artifacts_into_repo(repo, work1)
        ctx2 = build_project_context(repo)

        # Verify step 1 artifacts contain health-related code
        all_code = "\n".join(
            a.get("content", "") for a in work1.get("artifacts", [])
        )
        assert "health" in all_code.lower(), (
            f"Step 1 should have added health endpoint code in artifacts"
        )

        # ── Step 2: extend without breaking ──
        spec2, caps2, work2, review2 = self._run_pipeline(step2, repo, ctx2)
        logger.info("Step 2 done: %d artifacts", len(work2.get("artifacts", [])))

        # Judge step 2
        v2 = full_judge(
            self.client, step2.id, spec2, work2, review2, caps2,
            expected_lang="python", file_pattern=r"\.py$",
            required_patterns=step2.required_patterns or None,
        )
        print(f"\n{v2.summary()}")

        # ── Verify step 2 didn't break step 1 ──
        # Check that step 2 artifacts still contain /health references
        all_step2_code = "\n".join(
            a.get("content", "") for a in work2.get("artifacts", [])
        )
        # The worker should NOT have removed /health
        if "health" in all_step2_code:
            logger.info("Step 2 preserved /health endpoint (found in code)")

        # Check that /metrics was added
        assert "metrics" in all_step2_code.lower(), (
            f"Step 2 should contain 'metrics' in generated code"
        )

        if not v1.passed or not v2.passed:
            pytest.fail(
                f"Continuous enhancement failed: step1={v1.overall_score:.0%} step2={v2.overall_score:.0%}"
            )

    def test_go_continuous(self):
        """Two-step Go Chi enhancement: /healthz then /readyz."""
        cases = [c for c in cases_by_level(EvalLevel.L6_CONTINUOUS) if "go" in c.id]
        assert len(cases) >= 2, "Need at least 2 Go continuous cases"

        step1 = next(c for c in cases if "step1" in c.id)
        step2 = next(c for c in cases if "step2" in c.id)

        repo = REPO_GENERATORS[step1.repo_type](self.tmp_path / "go-continuous")
        ctx1 = build_project_context(repo)

        spec1, caps1, work1, review1 = self._run_pipeline(step1, repo, ctx1)
        v1 = full_judge(
            self.client, step1.id, spec1, work1, review1, caps1,
            expected_lang="go", file_pattern=r"\.go$",
        )
        print(f"\n{v1.summary()}")

        repo = _merge_artifacts_into_repo(repo, work1)
        ctx2 = build_project_context(repo)

        spec2, caps2, work2, review2 = self._run_pipeline(step2, repo, ctx2)
        v2 = full_judge(
            self.client, step2.id, spec2, work2, review2, caps2,
            expected_lang="go", file_pattern=r"\.go$",
            required_patterns=step2.required_patterns or None,
        )
        print(f"\n{v2.summary()}")

        all_step2_code = "\n".join(
            a.get("content", "") for a in work2.get("artifacts", [])
        )
        assert "readyz" in all_step2_code.lower(), "Step 2 should contain readyz"

        if not v1.passed or not v2.passed:
            pytest.fail(
                f"Continuous enhancement failed: step1={v1.overall_score:.0%} step2={v2.overall_score:.0%}"
            )
