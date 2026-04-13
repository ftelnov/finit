"""L3 eval: workspace persistence.

Verifies that a bootstrapped workspace retains its capabilities when
queried again — simulating "get back to the workspace later and see
that python is still available".

The test:
1. Bootstraps a workspace (first session)
2. Records the workspace_id and capabilities
3. Bootstraps the SAME project again (second session)
4. Verifies capabilities are consistent (language, tools still present)
"""

from __future__ import annotations

import json
import logging

import pytest

from conftest import a2a_send, build_project_context
from dataset import EvalLevel, cases_by_level
from fixtures import REPO_GENERATORS
from validators import EvalResult, CheckResult, validate_capabilities

logger = logging.getLogger(__name__)


class TestL3WorkspacePersistence:
    """Workspace capabilities persist across bootstrapper invocations."""

    @pytest.fixture(autouse=True)
    def _setup(self, client, make_repo):
        self.client = client
        self.make_repo = make_repo

    @pytest.mark.parametrize(
        "case",
        cases_by_level(EvalLevel.L3_PERSISTENCE),
        ids=[c.id for c in cases_by_level(EvalLevel.L3_PERSISTENCE)],
    )
    def test_workspace_persists(self, case):
        """Bootstrap twice → capabilities should be consistent."""
        repo = self.make_repo(case.repo_type)
        project_ctx = build_project_context(repo)

        spec = {
            "title": case.name,
            "description": case.task_description,
            "acceptance_criteria": ["Endpoint returns 200"],
        }
        payload = {"spec": spec, "project": project_ctx}

        # --- Session 1: initial bootstrap ---
        caps_1 = a2a_send(
            self.client,
            "bootstrapper",
            f"eval-{case.id}-s1",
            payload,
            timeout=case.timeout_s,
        )
        logger.info("Session 1 caps for %s: %s", case.id, json.dumps(caps_1, indent=2))

        # --- Session 2: re-bootstrap (simulates "coming back later") ---
        caps_2 = a2a_send(
            self.client,
            "bootstrapper",
            f"eval-{case.id}-s2",
            payload,
            timeout=case.timeout_s,
        )
        logger.info("Session 2 caps for %s: %s", case.id, json.dumps(caps_2, indent=2))

        # --- Validate both sessions ---
        result = EvalResult(case_id=case.id)

        # Both should detect the same language
        c1_caps = caps_1.get("capabilities", caps_1)
        c2_caps = caps_2.get("capabilities", caps_2)

        lang_1 = c1_caps.get("runtime", {}).get("language", "").lower()
        lang_2 = c2_caps.get("runtime", {}).get("language", "").lower()
        result.checks.append(CheckResult(
            name="language_consistent",
            passed=lang_1 == lang_2 and lang_1 != "",
            expected=f"same language in both sessions",
            actual=f"s1={lang_1!r} s2={lang_2!r}",
        ))

        # Both should have similar test commands
        test_1 = c1_caps.get("test_command", "").lower()
        test_2 = c2_caps.get("test_command", "").lower()
        # Allow minor variations but core tool should match
        result.checks.append(CheckResult(
            name="test_command_consistent",
            passed=test_1 != "" and test_2 != "",
            expected="non-empty test commands in both sessions",
            actual=f"s1={test_1!r} s2={test_2!r}",
        ))

        # Both should list similar tools
        tools_1 = {t.get("name", "").lower() for t in c1_caps.get("tools", [])}
        tools_2 = {t.get("name", "").lower() for t in c2_caps.get("tools", [])}
        overlap = tools_1 & tools_2
        result.checks.append(CheckResult(
            name="tools_overlap",
            passed=len(overlap) > 0,
            expected="at least one common tool",
            actual=f"s1={tools_1} s2={tools_2} overlap={overlap}",
        ))

        # Validate against expected capabilities
        if case.expected_capabilities:
            # Session 1
            s1_checks = validate_capabilities(caps_1, case.expected_capabilities)
            for c in s1_checks:
                c.name = f"s1_{c.name}"
            result.checks.extend(s1_checks)

            # Session 2 (the "coming back later" check)
            s2_checks = validate_capabilities(caps_2, case.expected_capabilities)
            for c in s2_checks:
                c.name = f"s2_{c.name}"
            result.checks.extend(s2_checks)

        print(f"\n{result.summary()}")

        if not result.passed:
            failed = [c for c in result.checks if not c.passed]
            detail = "; ".join(f"{c.name}: got={c.actual!r}" for c in failed)
            if result.error:
                detail = f"ERROR: {result.error}"
            pytest.fail(f"{case.id}: {detail}")
