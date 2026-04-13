"""L4 eval: full pipeline planner → bootstrapper → worker → reviewer.

Tests the complete agent pipeline with a real LLM — each agent feeds
into the next, and we validate:
- Planner produces a valid spec with acceptance criteria
- Bootstrapper detects the right environment
- Worker generates code in the correct language
- Reviewer produces a structured verdict
"""

from __future__ import annotations

import json
import logging

import pytest

from conftest import a2a_send, build_project_context
from dataset import EvalLevel, cases_by_level
from fixtures import REPO_GENERATORS
from validators import (
    EvalResult,
    CheckResult,
    validate_capabilities,
    validate_worker_output,
    validate_review,
)

logger = logging.getLogger(__name__)


class TestL4FullPipeline:
    """Complete agent pipeline with real LLM."""

    @pytest.fixture(autouse=True)
    def _setup(self, client, make_repo):
        self.client = client
        self.make_repo = make_repo

    @pytest.mark.parametrize(
        "case",
        cases_by_level(EvalLevel.L4_FULL_PIPELINE),
        ids=[c.id for c in cases_by_level(EvalLevel.L4_FULL_PIPELINE)],
    )
    def test_full_pipeline(self, case):
        """planner → bootstrapper → worker → reviewer, all with real LLM."""
        repo = self.make_repo(case.repo_type)
        project_ctx = build_project_context(repo)
        result = EvalResult(case_id=case.id)

        try:
            # ---- Step 1: Planner generates spec ----
            plan_payload = {
                "task_description": case.task_description,
                "project_context": project_ctx,
            }
            spec = a2a_send(
                self.client,
                "planner",
                f"eval-{case.id}-plan",
                plan_payload,
                timeout=case.timeout_s,
            )
            logger.info("Planner spec for %s: %s", case.id, json.dumps(spec, indent=2))

            # Validate spec structure
            result.checks.append(CheckResult(
                name="spec_has_title",
                passed=bool(spec.get("title")),
                expected="non-empty title",
                actual=spec.get("title", ""),
            ))
            result.checks.append(CheckResult(
                name="spec_has_criteria",
                passed=len(spec.get("acceptance_criteria", [])) > 0,
                expected="at least one acceptance criterion",
                actual=f"{len(spec.get('acceptance_criteria', []))} criteria",
            ))
            result.checks.append(CheckResult(
                name="spec_has_test_plan",
                passed=bool(spec.get("test_plan")),
                expected="test_plan present",
                actual=str(bool(spec.get("test_plan"))),
            ))

            # ---- Step 2: Bootstrapper prepares workspace ----
            boot_payload = {
                "spec": spec,
                "project": project_ctx,
            }
            capabilities = a2a_send(
                self.client,
                "bootstrapper",
                f"eval-{case.id}-boot",
                boot_payload,
                timeout=case.timeout_s,
            )
            logger.info("Bootstrapper caps for %s: %s", case.id, json.dumps(capabilities, indent=2))

            if case.expected_capabilities:
                cap_checks = validate_capabilities(capabilities, case.expected_capabilities)
                result.checks.extend(cap_checks)

            # ---- Step 3: Worker generates code ----
            worker_payload = {
                "spec": spec,
                "workspace": capabilities,
                "project": project_ctx,
            }
            worker_result = a2a_send(
                self.client,
                "worker",
                f"eval-{case.id}-work",
                worker_payload,
                timeout=case.timeout_s,
            )
            logger.info("Worker result for %s: %s", case.id, json.dumps(worker_result, indent=2))

            if case.expected_worker:
                worker_checks = validate_worker_output(worker_result, case.expected_worker)
                result.checks.extend(worker_checks)

            # Basic worker output checks
            result.checks.append(CheckResult(
                name="worker_has_artifacts",
                passed=len(worker_result.get("artifacts", [])) > 0,
                expected="at least one artifact",
                actual=f"{len(worker_result.get('artifacts', []))} artifacts",
            ))
            result.checks.append(CheckResult(
                name="worker_has_summary",
                passed=bool(worker_result.get("summary")),
                expected="non-empty summary",
                actual=worker_result.get("summary", "")[:80],
            ))

            # ---- Step 4: Reviewer evaluates ----
            review_payload = {
                "spec": spec,
                "artifacts": worker_result.get("artifacts", []),
                "test_results": worker_result.get("test_results", {}),
            }
            review_result = a2a_send(
                self.client,
                "reviewer",
                f"eval-{case.id}-review",
                review_payload,
                timeout=case.timeout_s,
            )
            logger.info("Reviewer result for %s: %s", case.id, json.dumps(review_result, indent=2))

            if case.expected_review:
                review_checks = validate_review(review_result, case.expected_review)
                result.checks.extend(review_checks)

        except Exception as exc:
            result.error = str(exc)
            logger.error("Pipeline for %s failed: %s", case.id, exc, exc_info=True)

        # Report
        print(f"\n{result.summary()}")

        if not result.passed:
            failed = [c for c in result.checks if not c.passed]
            detail = "; ".join(f"{c.name}: expected={c.expected!r} got={c.actual!r}" for c in failed)
            if result.error:
                detail = f"ERROR: {result.error}"
            pytest.fail(f"{case.id}: {detail}")
