"""L7 evals: memory rules and constraint compliance.

Tests that agents follow explicit rules:
- Never touch forbidden files (manifest.json)
- Never use print() — use logging
- Always add docstrings to public functions

Rules are injected into the task description and checked statically + via LLM judge.
"""

from __future__ import annotations

import json
import logging

import pytest

from conftest import a2a_send, build_project_context
from dataset import EvalLevel, cases_by_level
from judge import full_judge

logger = logging.getLogger(__name__)


class TestL7RuleCompliance:
    """Rule-following evaluation tests."""

    @pytest.fixture(autouse=True)
    def _setup(self, client, make_repo):
        self.client = client
        self.make_repo = make_repo

    @pytest.mark.parametrize(
        "case",
        cases_by_level(EvalLevel.L7_RULE_COMPLIANCE),
        ids=[c.id for c in cases_by_level(EvalLevel.L7_RULE_COMPLIANCE)],
    )
    def test_rule_compliance(self, case):
        repo = self.make_repo(case.repo_type)
        project_ctx = build_project_context(repo)

        # Build task description with rules injected
        task_desc = case.task_description
        if case.memory_rules:
            rules_text = "\n".join(f"- {r}" for r in case.memory_rules)
            task_desc += f"\n\n## Workspace Rules (MUST follow)\n{rules_text}"

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

        # ── Worker (with rules in context) ──
        worker_payload = {
            "spec": spec,
            "workspace": capabilities,
            "project": project_ctx,
        }
        if case.memory_rules:
            worker_payload["rules"] = case.memory_rules

        worker_result = a2a_send(
            self.client, "worker", f"eval-{case.id}-work",
            worker_payload,
            timeout=case.timeout_s,
        )

        # ── Reviewer ──
        review_result = a2a_send(
            self.client, "reviewer", f"eval-{case.id}-review",
            {"spec": spec, "artifacts": worker_result.get("artifacts", []),
             "test_results": worker_result.get("test_results", {})},
            timeout=case.timeout_s,
        )

        # ── Judge with rules ──
        verdict = full_judge(
            client=self.client,
            case_id=case.id,
            spec=spec,
            worker_result=worker_result,
            review_result=review_result,
            capabilities=capabilities,
            expected_lang=case.expected_capabilities.language if case.expected_capabilities else "python",
            file_pattern=case.expected_worker.files_pattern if case.expected_worker else r"\.py$",
            required_patterns=case.required_patterns or None,
            forbidden_patterns=case.forbidden_patterns or None,
            forbidden_files=case.forbidden_files or None,
            rules=case.memory_rules or None,
            use_llm_judge=case.use_llm_judge,
        )

        print(f"\n{verdict.summary()}")

        # Rule compliance is strict — any forbidden pattern/file violation fails
        forbidden_checks = [
            c for c in verdict.static_checks
            if c.name in ("forbidden_patterns", "forbidden_files_untouched")
        ]
        rule_violations = [c for c in forbidden_checks if not c.passed]
        if rule_violations:
            detail = "; ".join(
                f"{c.name}: expected={c.expected!r} actual={c.actual!r}"
                for c in rule_violations
            )
            pytest.fail(f"{case.id} RULE VIOLATION: {detail}")

        if not verdict.passed:
            failed = [c for c in verdict.static_checks if not c.passed]
            detail = "; ".join(f"{c.name}" for c in failed)
            if verdict.error:
                detail = f"ERROR: {verdict.error}"
            pytest.fail(f"{case.id}: {detail} (score={verdict.overall_score:.0%})")
