"""LLM-as-a-Judge and static verification for eval outcomes.

Two layers of verification:
1. Static checks — deterministic assertions on structure, file patterns, tool calls
2. LLM judge — calls the LLM to score quality dimensions against a rubric

The judge is invoked AFTER an agent pipeline run, scoring the final artifacts.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

LLM_URL = os.environ.get("LLM_URL", os.environ.get("LLM_ROUTER_URL", "http://localhost:8081"))
LLM_MODEL = os.environ.get("LLM_MODEL", "/opt/MiniMaxAI/MiniMax-M2.7")
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret")

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class JudgeScore:
    """Score for a single evaluation dimension."""
    dimension: str
    score: int          # 0-5
    max_score: int = 5
    reasoning: str = ""
    evidence: str = ""


@dataclass
class StaticCheck:
    """Result of a deterministic check."""
    name: str
    passed: bool
    expected: str
    actual: str
    detail: str = ""


@dataclass
class JudgeVerdict:
    """Complete judge verdict for one eval case."""
    case_id: str
    static_checks: list[StaticCheck] = field(default_factory=list)
    llm_scores: list[JudgeScore] = field(default_factory=list)
    error: str | None = None

    @property
    def static_pass_rate(self) -> float:
        if not self.static_checks:
            return 1.0
        return sum(1 for c in self.static_checks if c.passed) / len(self.static_checks)

    @property
    def llm_avg_score(self) -> float:
        if not self.llm_scores:
            return 0.0
        return sum(s.score for s in self.llm_scores) / sum(s.max_score for s in self.llm_scores)

    @property
    def overall_score(self) -> float:
        """Combined score: 50% static, 50% LLM judge."""
        if self.error:
            return 0.0
        static = self.static_pass_rate
        llm = self.llm_avg_score if self.llm_scores else static
        return (static + llm) / 2.0

    @property
    def passed(self) -> bool:
        return self.error is None and self.static_pass_rate >= 0.8 and self.overall_score >= 0.5

    def summary(self) -> str:
        lines = [f"[{'PASS' if self.passed else 'FAIL'}] {self.case_id} — score={self.overall_score:.0%}"]
        if self.error:
            lines.append(f"  ERROR: {self.error}")
        if self.static_checks:
            passed = sum(1 for c in self.static_checks if c.passed)
            lines.append(f"  Static: {passed}/{len(self.static_checks)}")
            for c in self.static_checks:
                mark = "+" if c.passed else "x"
                lines.append(f"    [{mark}] {c.name}")
                if not c.passed:
                    lines.append(f"        expected={c.expected!r} actual={c.actual!r}")
        if self.llm_scores:
            lines.append(f"  LLM Judge: {self.llm_avg_score:.0%}")
            for s in self.llm_scores:
                lines.append(f"    {s.dimension}: {s.score}/{s.max_score} — {s.reasoning}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------

def check_artifacts_exist(worker_result: dict[str, Any]) -> StaticCheck:
    """Worker produced at least one code artifact."""
    artifacts = worker_result.get("artifacts", [])
    return StaticCheck(
        name="artifacts_exist",
        passed=len(artifacts) > 0,
        expected=">=1 artifact",
        actual=f"{len(artifacts)} artifacts",
    )


def check_tests_ran(worker_result: dict[str, Any]) -> StaticCheck:
    """Worker ran tests (has test_results with a command)."""
    tr = worker_result.get("test_results", {})
    has_cmd = bool(tr.get("command") and tr["command"] != "N/A")
    return StaticCheck(
        name="tests_ran",
        passed=has_cmd,
        expected="test command executed",
        actual=tr.get("command", "N/A"),
    )


def check_tests_passed(worker_result: dict[str, Any]) -> StaticCheck:
    """Tests exited with code 0."""
    tr = worker_result.get("test_results", {})
    exit_code = tr.get("exit_code", -1)
    return StaticCheck(
        name="tests_passed",
        passed=exit_code == 0,
        expected="exit_code=0",
        actual=f"exit_code={exit_code}",
        detail=tr.get("stderr", "")[:200] if exit_code != 0 else "",
    )


def check_language_correct(
    worker_result: dict[str, Any],
    expected_lang: str,
    file_pattern: str,
) -> StaticCheck:
    """Generated files match expected language pattern (e.g. r'\\.py$')."""
    artifacts = worker_result.get("artifacts", [])
    paths = [a.get("path", "") for a in artifacts if a.get("type") == "code_change"]
    matching = [p for p in paths if re.search(file_pattern, p)]
    return StaticCheck(
        name="language_correct",
        passed=len(matching) > 0,
        expected=f"files matching {file_pattern} (lang={expected_lang})",
        actual=str(paths),
    )


def check_code_contains(
    worker_result: dict[str, Any],
    patterns: list[str],
    label: str = "required_patterns",
) -> StaticCheck:
    """Generated code contains required patterns (substrings)."""
    artifacts = worker_result.get("artifacts", [])
    all_code = "\n".join(a.get("content", "") for a in artifacts)
    found = [p for p in patterns if p in all_code]
    threshold = max(1, len(patterns) // 2)
    return StaticCheck(
        name=label,
        passed=len(found) >= threshold,
        expected=f">={threshold} of {patterns}",
        actual=f"found {found}",
    )


def check_code_not_contains(
    worker_result: dict[str, Any],
    patterns: list[str],
    label: str = "forbidden_patterns",
) -> StaticCheck:
    """Generated code does NOT contain forbidden patterns."""
    artifacts = worker_result.get("artifacts", [])
    all_code = "\n".join(a.get("content", "") for a in artifacts)
    violations = [p for p in patterns if p in all_code]
    return StaticCheck(
        name=label,
        passed=len(violations) == 0,
        expected="none present",
        actual=f"found {violations}" if violations else "none",
    )


def check_review_verdict(review_result: dict[str, Any], expected: str | None) -> StaticCheck:
    """Reviewer gave the expected verdict."""
    verdict = review_result.get("verdict", "UNKNOWN")
    if expected is None:
        return StaticCheck(
            name="review_has_verdict",
            passed=verdict in ("PASS", "FAIL"),
            expected="PASS or FAIL",
            actual=verdict,
        )
    return StaticCheck(
        name=f"review_verdict_{expected.lower()}",
        passed=verdict == expected,
        expected=expected,
        actual=verdict,
    )


def check_criteria_coverage(review_result: dict[str, Any]) -> StaticCheck:
    """Reviewer checked at least one acceptance criterion."""
    criteria = review_result.get("criteria_met", [])
    return StaticCheck(
        name="criteria_coverage",
        passed=len(criteria) > 0,
        expected=">=1 criterion evaluated",
        actual=f"{len(criteria)} criteria",
    )


def check_file_not_touched(
    worker_result: dict[str, Any],
    forbidden_files: list[str],
) -> StaticCheck:
    """Worker did NOT read or write forbidden files."""
    artifacts = worker_result.get("artifacts", [])
    paths_written = {a.get("path", "") for a in artifacts}

    # Check tool call log if available
    tool_calls = worker_result.get("tool_calls", [])
    paths_read = set()
    for tc in tool_calls:
        if tc.get("name") == "read_file":
            paths_read.add(tc.get("args", {}).get("path", ""))

    all_touched = paths_written | paths_read
    violations = [f for f in forbidden_files if any(f in p for p in all_touched)]
    return StaticCheck(
        name="forbidden_files_untouched",
        passed=len(violations) == 0,
        expected=f"never touch {forbidden_files}",
        actual=f"touched {violations}" if violations else "none touched",
    )


def check_env_detection(
    capabilities: dict[str, Any],
    expected_lang: str,
    expected_framework: str | None = None,
    lang_aliases: list[str] | None = None,
    framework_aliases: list[str] | None = None,
) -> list[StaticCheck]:
    """Verify bootstrapper detected the right environment."""
    checks = []
    caps = capabilities.get("capabilities", capabilities)
    runtime = caps.get("runtime", {})

    detected_lang = runtime.get("language", "").lower().strip()
    acceptable_langs = {expected_lang.lower()}
    if lang_aliases:
        acceptable_langs |= {a.lower() for a in lang_aliases}

    checks.append(StaticCheck(
        name="env_language",
        passed=detected_lang in acceptable_langs,
        expected=expected_lang,
        actual=detected_lang,
        detail=f"acceptable: {acceptable_langs}",
    ))

    if expected_framework:
        detected_fw = runtime.get("framework", "").lower().strip()
        acceptable_fws = {expected_framework.lower()}
        if framework_aliases:
            acceptable_fws |= {a.lower() for a in framework_aliases}
        fw_match = any(a in detected_fw or detected_fw in a for a in acceptable_fws if a)
        checks.append(StaticCheck(
            name="env_framework",
            passed=fw_match,
            expected=expected_framework,
            actual=detected_fw,
            detail=f"acceptable: {acceptable_fws}",
        ))

    return checks


# ---------------------------------------------------------------------------
# LLM-as-a-Judge
# ---------------------------------------------------------------------------

JUDGE_SYSTEM = """\
You are a code review judge evaluating AI-generated code against a specification.

Score each dimension from 0-5:
- 0: Completely wrong / missing
- 1: Major issues, barely addresses the spec
- 2: Partial implementation, significant gaps
- 3: Mostly correct, some issues
- 4: Good implementation, minor issues only
- 5: Excellent, fully meets spec

Respond with ONLY valid JSON (no markdown, no code blocks):
{
  "scores": [
    {
      "dimension": "<dimension_name>",
      "score": <0-5>,
      "reasoning": "<one sentence explanation>"
    }
  ]
}
"""

JUDGE_DIMENSIONS = {
    "correctness": "Does the code correctly implement the specification? Are all acceptance criteria met?",
    "code_quality": "Is the code clean, idiomatic, and following the project's conventions? No dead code, no placeholders?",
    "test_quality": "Are the tests meaningful? Do they cover the main functionality and edge cases?",
    "environment_fit": "Does the code fit the project's language, framework, and tooling? No wrong-language code?",
}

RULE_COMPLIANCE_DIMENSION = (
    "rule_compliance",
    "Did the agent follow all explicit rules? Were forbidden files left untouched? Were constraints respected?",
)


def _call_judge_llm(
    client: httpx.Client,
    spec: dict[str, Any],
    artifacts: list[dict[str, Any]],
    test_results: dict[str, Any],
    dimensions: dict[str, str],
    rules: list[str] | None = None,
) -> list[JudgeScore]:
    """Call the LLM to score the output."""

    user_msg = f"## Specification\n{json.dumps(spec, indent=2)}\n\n"
    user_msg += "## Generated Code Artifacts\n"
    for art in artifacts:
        path = art.get("path", "unknown")
        content = art.get("content", "")
        if len(content) > 3000:
            content = content[:3000] + "\n... (truncated)"
        user_msg += f"\n### {path}\n```\n{content}\n```\n"

    user_msg += f"\n## Test Results\n```\n{json.dumps(test_results, indent=2)}\n```\n"

    if rules:
        user_msg += "\n## Rules That Must Be Followed\n"
        for r in rules:
            user_msg += f"- {r}\n"

    user_msg += "\n## Dimensions to Score\n"
    for dim, desc in dimensions.items():
        user_msg += f"- **{dim}**: {desc}\n"

    user_msg += "\nScore each dimension 0-5. Respond with JSON only."

    try:
        resp = client.post(
            f"{LLM_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {JWT_SECRET}",
                "X-Agent-ID": "judge",
            },
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                "max_tokens": 2048,
                "temperature": 0.1,
            },
            timeout=180.0,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

        # Strip think tags and parse JSON
        content = _THINK_RE.sub("", content).strip()
        if content.startswith("```"):
            lines = content.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)

        result = json.loads(content)
        scores = []
        for s in result.get("scores", []):
            scores.append(JudgeScore(
                dimension=s["dimension"],
                score=min(5, max(0, int(s["score"]))),
                reasoning=s.get("reasoning", ""),
            ))
        return scores

    except Exception as e:
        logger.error("LLM judge call failed: %s", e)
        return [JudgeScore(
            dimension="judge_error",
            score=0,
            reasoning=f"Judge call failed: {e}",
        )]


def judge_worker_output(
    client: httpx.Client,
    spec: dict[str, Any],
    worker_result: dict[str, Any],
    dimensions: dict[str, str] | None = None,
    rules: list[str] | None = None,
) -> list[JudgeScore]:
    """Score worker output using LLM-as-a-Judge."""
    dims = dimensions or JUDGE_DIMENSIONS
    if rules:
        dims = {**dims, RULE_COMPLIANCE_DIMENSION[0]: RULE_COMPLIANCE_DIMENSION[1]}

    return _call_judge_llm(
        client=client,
        spec=spec,
        artifacts=worker_result.get("artifacts", []),
        test_results=worker_result.get("test_results", {}),
        dimensions=dims,
        rules=rules,
    )


# ---------------------------------------------------------------------------
# Composite judge: static + LLM
# ---------------------------------------------------------------------------

def full_judge(
    client: httpx.Client,
    case_id: str,
    spec: dict[str, Any],
    worker_result: dict[str, Any],
    review_result: dict[str, Any] | None = None,
    capabilities: dict[str, Any] | None = None,
    expected_lang: str | None = None,
    expected_framework: str | None = None,
    file_pattern: str | None = None,
    required_patterns: list[str] | None = None,
    forbidden_patterns: list[str] | None = None,
    forbidden_files: list[str] | None = None,
    rules: list[str] | None = None,
    use_llm_judge: bool = True,
) -> JudgeVerdict:
    """Run full static + LLM judge evaluation."""
    verdict = JudgeVerdict(case_id=case_id)

    try:
        # ── Static checks ────────────────────────────────────
        verdict.static_checks.append(check_artifacts_exist(worker_result))
        verdict.static_checks.append(check_tests_ran(worker_result))
        verdict.static_checks.append(check_tests_passed(worker_result))

        if expected_lang and file_pattern:
            verdict.static_checks.append(
                check_language_correct(worker_result, expected_lang, file_pattern)
            )

        if required_patterns:
            verdict.static_checks.append(
                check_code_contains(worker_result, required_patterns)
            )

        if forbidden_patterns:
            verdict.static_checks.append(
                check_code_not_contains(worker_result, forbidden_patterns)
            )

        if forbidden_files:
            verdict.static_checks.append(
                check_file_not_touched(worker_result, forbidden_files)
            )

        if capabilities and expected_lang:
            env_checks = check_env_detection(
                capabilities, expected_lang, expected_framework,
            )
            verdict.static_checks.extend(env_checks)

        if review_result:
            verdict.static_checks.append(check_review_verdict(review_result, None))
            verdict.static_checks.append(check_criteria_coverage(review_result))

        # ── LLM judge ────────────────────────────────────────
        if use_llm_judge:
            verdict.llm_scores = judge_worker_output(
                client=client,
                spec=spec,
                worker_result=worker_result,
                rules=rules,
            )

    except Exception as e:
        verdict.error = str(e)
        logger.error("Judge failed for %s: %s", case_id, e, exc_info=True)

    return verdict
