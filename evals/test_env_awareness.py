"""L1 & L2 eval: environment awareness.

L1 — Project type detection: the bootstrapper correctly identifies language,
     framework, tools, and commands from a dummy git repository.

L2 — Environment-aware execution: given correct workspace capabilities,
     the worker generates code in the RIGHT language (not Go for a Python project).
"""

from __future__ import annotations

import json
import logging

import pytest

from conftest import AGENT_URLS, a2a_send, build_project_context
from dataset import ALL_CASES, EvalLevel, cases_by_level
from fixtures import REPO_GENERATORS
from validators import (
    CheckResult,
    EvalResult,
    validate_capabilities,
    validate_worker_output,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# L1: Bootstrapper detects project type
# ---------------------------------------------------------------------------

class TestL1ProjectDetection:
    """Bootstrapper receives a project and correctly identifies its stack."""

    @pytest.fixture(autouse=True)
    def _setup(self, client, make_repo):
        self.client = client
        self.make_repo = make_repo

    @pytest.mark.parametrize(
        "case",
        cases_by_level(EvalLevel.L1_DETECTION),
        ids=[c.id for c in cases_by_level(EvalLevel.L1_DETECTION)],
    )
    def test_bootstrapper_detection(self, case):
        """Bootstrapper correctly detects language, framework, tools for each project type."""
        repo = self.make_repo(case.repo_type)
        project_ctx = build_project_context(repo)

        # Call bootstrapper with spec + project context
        payload = {
            "spec": {
                "title": case.name,
                "description": case.task_description,
                "acceptance_criteria": [
                    "Endpoint returns 200 with JSON response",
                    "Response includes status field",
                ],
            },
            "project": project_ctx,
        }

        result = EvalResult(case_id=case.id)

        try:
            capabilities = a2a_send(
                self.client,
                "bootstrapper",
                f"eval-{case.id}",
                payload,
                timeout=case.timeout_s,
            )
            logger.info("Bootstrapper response for %s: %s", case.id, json.dumps(capabilities, indent=2))

            # Validate
            assert case.expected_capabilities is not None
            checks = validate_capabilities(capabilities, case.expected_capabilities)
            result.checks = checks

        except Exception as exc:
            result.error = str(exc)
            logger.error("Case %s failed: %s", case.id, exc)

        # Report
        print(f"\n{result.summary()}")

        # Fail test if any check failed
        if not result.passed:
            failed = [c for c in result.checks if not c.passed]
            detail = "; ".join(f"{c.name}: expected={c.expected!r} got={c.actual!r}" for c in failed)
            if result.error:
                detail = f"ERROR: {result.error}"
            pytest.fail(f"{case.id}: {detail}")


# ---------------------------------------------------------------------------
# L2: Worker generates correct language
# ---------------------------------------------------------------------------

class TestL2EnvironmentAwareExecution:
    """Worker generates code in the correct language given workspace capabilities."""

    @pytest.fixture(autouse=True)
    def _setup(self, client, make_repo):
        self.client = client
        self.make_repo = make_repo

    @pytest.mark.parametrize(
        "case",
        cases_by_level(EvalLevel.L2_ENV_AWARE),
        ids=[c.id for c in cases_by_level(EvalLevel.L2_ENV_AWARE)],
    )
    def test_worker_language_awareness(self, case):
        """Worker generates code in the project's language, not some default."""
        repo = self.make_repo(case.repo_type)
        project_ctx = build_project_context(repo)

        # Step 1: Get capabilities from bootstrapper
        boot_payload = {
            "spec": {
                "title": case.name,
                "description": case.task_description,
                "acceptance_criteria": [
                    "Endpoint returns 200 with JSON response",
                    "Response includes status field",
                ],
            },
            "project": project_ctx,
        }

        capabilities = a2a_send(
            self.client,
            "bootstrapper",
            f"eval-{case.id}-boot",
            boot_payload,
            timeout=case.timeout_s,
        )

        # Validate capabilities first
        result = EvalResult(case_id=case.id)

        if case.expected_capabilities:
            cap_checks = validate_capabilities(capabilities, case.expected_capabilities)
            result.checks.extend(cap_checks)

        # Step 2: Send spec + capabilities to worker
        spec = {
            "title": case.name,
            "description": case.task_description,
            "acceptance_criteria": [
                "Endpoint returns 200 with JSON response",
                "Response includes status field",
                "Includes at least one test",
            ],
            "test_plan": {"unit_tests": ["TestHealth"], "commands": ["run tests"]},
            "files_likely_affected": [],
            "domains": [],
        }

        worker_payload = {
            "spec": spec,
            "workspace": capabilities,
            "project": project_ctx,
        }

        try:
            worker_result = a2a_send(
                self.client,
                "worker",
                f"eval-{case.id}-work",
                worker_payload,
                timeout=case.timeout_s,
            )
            logger.info("Worker response for %s: %s", case.id, json.dumps(worker_result, indent=2))

            # Validate worker output
            if case.expected_worker:
                worker_checks = validate_worker_output(worker_result, case.expected_worker)
                result.checks.extend(worker_checks)

        except Exception as exc:
            result.error = str(exc)
            logger.error("Worker for %s failed: %s", case.id, exc)

        print(f"\n{result.summary()}")

        if not result.passed:
            failed = [c for c in result.checks if not c.passed]
            detail = "; ".join(f"{c.name}: expected={c.expected!r} got={c.actual!r}" for c in failed)
            if result.error:
                detail = f"ERROR: {result.error}"
            pytest.fail(f"{case.id}: {detail}")
