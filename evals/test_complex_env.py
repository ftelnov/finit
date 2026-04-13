"""L8 evals: complex environment challenges.

Tests that stress fundamentally different capabilities:
1. Refactoring existing code without breaking it
2. Monorepo with shared modules across services
3. Custom test runners (not the usual jest/pytest)
"""

from __future__ import annotations

import json
import logging

import pytest

from conftest import a2a_send, build_project_context
from dataset import EvalLevel, cases_by_level
from judge import full_judge

logger = logging.getLogger(__name__)


class TestL8ComplexEnv:
    """Complex environment challenges with judge scoring."""

    @pytest.fixture(autouse=True)
    def _setup(self, client, make_repo):
        self.client = client
        self.make_repo = make_repo

    @pytest.mark.parametrize(
        "case",
        cases_by_level(EvalLevel.L8_COMPLEX_ENV),
        ids=[c.id for c in cases_by_level(EvalLevel.L8_COMPLEX_ENV)],
    )
    def test_complex_env(self, case):
        repo = self.make_repo(case.repo_type)
        project_ctx = build_project_context(repo)

        # Inject rules into task description if present
        task_desc = case.task_description
        if case.memory_rules:
            rules_text = "\n".join(f"- {r}" for r in case.memory_rules)
            task_desc += f"\n\n## Workspace Rules\n{rules_text}"

        # ── Planner ──
        spec = a2a_send(
            self.client, "planner", f"eval-{case.id}-plan",
            {"task_description": task_desc, "project_context": project_ctx},
            timeout=case.timeout_s,
        )
        logger.info("Spec for %s: %s", case.id, spec.get("title", "?"))

        # ── Bootstrapper ──
        capabilities = a2a_send(
            self.client, "bootstrapper", f"eval-{case.id}-boot",
            {"spec": spec, "project": project_ctx},
            timeout=case.timeout_s,
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
            expected_lang=case.expected_capabilities.language if case.expected_capabilities else None,
            expected_framework=case.expected_capabilities.framework if case.expected_capabilities else None,
            file_pattern=case.expected_worker.files_pattern if case.expected_worker else None,
            required_patterns=case.required_patterns or None,
            forbidden_patterns=case.forbidden_patterns or None,
            forbidden_files=case.forbidden_files or None,
            rules=case.memory_rules or None,
            use_llm_judge=case.use_llm_judge,
        )

        print(f"\n{verdict.summary()}")

        if not verdict.passed:
            failed = [c for c in verdict.static_checks if not c.passed]
            detail = "; ".join(f"{c.name}" for c in failed)
            if verdict.error:
                detail = f"ERROR: {verdict.error}"
            pytest.fail(f"{case.id}: {detail} (score={verdict.overall_score:.0%})")
