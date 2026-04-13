"""Validation and scoring helpers for eval assertions.

Validators check structural properties of agent responses against
expected outcomes, producing scored results with detailed diagnostics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from dataset import ExpectedCapabilities, ExpectedReview, ExpectedWorkerOutput


@dataclass
class CheckResult:
    """Result of a single validation check."""

    name: str
    passed: bool
    expected: str
    actual: str
    detail: str = ""


@dataclass
class EvalResult:
    """Aggregated result for one eval case."""

    case_id: str
    checks: list[CheckResult] = field(default_factory=list)
    error: str | None = None  # if the whole case failed (e.g. timeout, parse error)

    @property
    def passed(self) -> bool:
        return self.error is None and all(c.passed for c in self.checks)

    @property
    def score(self) -> float:
        if self.error:
            return 0.0
        if not self.checks:
            return 0.0
        return sum(1.0 for c in self.checks if c.passed) / len(self.checks)

    def summary(self) -> str:
        lines = [f"[{'PASS' if self.passed else 'FAIL'}] {self.case_id} — score={self.score:.0%}"]
        if self.error:
            lines.append(f"  ERROR: {self.error}")
        for c in self.checks:
            mark = "+" if c.passed else "x"
            lines.append(f"  [{mark}] {c.name}: expected={c.expected!r} actual={c.actual!r}")
            if c.detail:
                lines.append(f"       {c.detail}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Capability validators
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    return s.strip().lower()


def validate_capabilities(
    capabilities: dict[str, Any],
    expected: ExpectedCapabilities,
) -> list[CheckResult]:
    """Validate bootstrapper capabilities against expectations."""
    checks: list[CheckResult] = []
    caps = capabilities.get("capabilities", capabilities)

    # Language detection
    runtime = caps.get("runtime", {})
    detected_lang = _normalize(runtime.get("language", ""))
    acceptable = [_normalize(expected.language)] + [_normalize(a) for a in expected.language_aliases]
    checks.append(CheckResult(
        name="language_detection",
        passed=detected_lang in acceptable,
        expected=expected.language,
        actual=detected_lang,
        detail=f"acceptable: {acceptable}",
    ))

    # Version pattern
    if expected.version_pattern:
        detected_version = str(runtime.get("version", ""))
        version_match = bool(re.search(expected.version_pattern, detected_version))
        checks.append(CheckResult(
            name="version_pattern",
            passed=version_match,
            expected=expected.version_pattern,
            actual=detected_version,
        ))

    # Framework detection (fuzzy: "flask==3.0.3" matches "flask")
    if expected.framework:
        detected_fw = _normalize(runtime.get("framework", ""))
        fw_acceptable = [_normalize(expected.framework)] + [_normalize(a) for a in expected.framework_aliases]
        fw_match = any(acc in detected_fw or detected_fw in acc for acc in fw_acceptable if acc)
        checks.append(CheckResult(
            name="framework_detection",
            passed=fw_match,
            expected=expected.framework,
            actual=detected_fw,
            detail=f"acceptable: {fw_acceptable}",
        ))

    # Test command
    if expected.test_command_contains:
        test_cmd = _normalize(caps.get("test_command", ""))
        any_match = any(sub.lower() in test_cmd for sub in expected.test_command_contains)
        checks.append(CheckResult(
            name="test_command",
            passed=any_match,
            expected=f"contains one of {expected.test_command_contains}",
            actual=test_cmd,
        ))

    # Lint command (soft: pass if empty OR contains expected substring)
    if expected.lint_command_contains:
        lint_cmd = _normalize(caps.get("lint_command", ""))
        any_match = not lint_cmd or any(sub.lower() in lint_cmd for sub in expected.lint_command_contains)
        checks.append(CheckResult(
            name="lint_command",
            passed=any_match,
            expected=f"empty or contains one of {expected.lint_command_contains}",
            actual=lint_cmd,
        ))

    # Build command (soft: pass if empty OR contains expected substring)
    if expected.build_command_contains:
        build_cmd = _normalize(caps.get("build_command", ""))
        any_match = not build_cmd or any(sub.lower() in build_cmd for sub in expected.build_command_contains)
        checks.append(CheckResult(
            name="build_command",
            passed=any_match,
            expected=f"empty or contains one of {expected.build_command_contains}",
            actual=build_cmd,
        ))

    # Tools (also consider runtime language as implicitly present)
    if expected.tools_include:
        detected_tools = [_normalize(t.get("name", "")) for t in caps.get("tools", [])]
        # The runtime language itself counts as present (python, go, node, cargo, etc.)
        implicit_tools = {_normalize(runtime.get("language", ""))}
        # Common implicit mappings: python → pip, node → npm, rust → cargo
        lang = _normalize(runtime.get("language", ""))
        if lang in ("python", "py", "python3"):
            implicit_tools |= {"python", "python3", "pip", "pip3"}
        elif lang in ("go", "golang"):
            implicit_tools |= {"go"}
        elif lang in ("javascript", "js", "node", "nodejs", "typescript"):
            implicit_tools |= {"node", "npm", "npx"}
        elif lang in ("rust", "rs"):
            implicit_tools |= {"cargo", "rustc"}
        all_tools = detected_tools + list(implicit_tools)
        for tool_name in expected.tools_include:
            found = any(tool_name.lower() in t for t in all_tools)
            checks.append(CheckResult(
                name=f"tool_present_{tool_name}",
                passed=found,
                expected=tool_name,
                actual=str(detected_tools),
                detail=f"implicit from runtime: {implicit_tools}" if not any(tool_name.lower() in t for t in detected_tools) else "",
            ))

    return checks


# ---------------------------------------------------------------------------
# Worker output validators
# ---------------------------------------------------------------------------

def validate_worker_output(
    worker_response: dict[str, Any],
    expected: ExpectedWorkerOutput,
) -> list[CheckResult]:
    """Validate worker-generated code against expectations."""
    checks: list[CheckResult] = []

    artifacts = worker_response.get("artifacts", [])
    all_code = " ".join(
        a.get("content", "") for a in artifacts if a.get("type") == "code_change"
    )
    all_paths = [a.get("path", "") for a in artifacts if a.get("type") == "code_change"]

    # Language markers (at least one must be present)
    if expected.language_markers:
        # Check that at least half of markers are present (LLM might use different style)
        found = [m for m in expected.language_markers if m in all_code]
        threshold = max(1, len(expected.language_markers) // 2)
        checks.append(CheckResult(
            name="language_markers",
            passed=len(found) >= threshold,
            expected=f">={threshold} of {expected.language_markers}",
            actual=f"found {len(found)}: {found}",
        ))

    # Antimarkers (none should be present)
    if expected.language_antimarkers:
        violations = [m for m in expected.language_antimarkers if m in all_code]
        checks.append(CheckResult(
            name="language_antimarkers",
            passed=len(violations) == 0,
            expected="none present",
            actual=f"found {violations}" if violations else "none found",
        ))

    # File extension pattern
    if expected.files_pattern:
        matching = [p for p in all_paths if re.search(expected.files_pattern, p)]
        checks.append(CheckResult(
            name="file_extensions",
            passed=len(matching) > 0,
            expected=f"paths matching {expected.files_pattern}",
            actual=str(all_paths),
        ))

    # Has tests
    if expected.has_tests:
        test_results = worker_response.get("test_results", {})
        has_test_cmd = bool(test_results.get("command"))
        has_test_files = any("test" in p.lower() for p in all_paths)
        checks.append(CheckResult(
            name="has_tests",
            passed=has_test_cmd or has_test_files,
            expected="test command or test files present",
            actual=f"cmd={test_results.get('command', '')!r} files={all_paths}",
        ))

    return checks


# ---------------------------------------------------------------------------
# Review validators
# ---------------------------------------------------------------------------

def validate_review(
    review_response: dict[str, Any],
    expected: ExpectedReview,
) -> list[CheckResult]:
    """Validate reviewer output against expectations."""
    checks: list[CheckResult] = []

    # Valid JSON structure
    has_verdict = "verdict" in review_response
    checks.append(CheckResult(
        name="has_verdict",
        passed=has_verdict,
        expected="verdict field present",
        actual=str(list(review_response.keys())),
    ))

    # Verdict value
    if expected.verdict and has_verdict:
        checks.append(CheckResult(
            name="verdict_value",
            passed=review_response["verdict"] == expected.verdict,
            expected=expected.verdict,
            actual=review_response.get("verdict", ""),
        ))

    # Has criteria check
    if expected.has_criteria_check:
        criteria = review_response.get("criteria_met", [])
        checks.append(CheckResult(
            name="has_criteria_met",
            passed=len(criteria) > 0,
            expected="at least one criterion checked",
            actual=f"{len(criteria)} criteria",
        ))

    # Has findings
    findings = review_response.get("findings", [])
    checks.append(CheckResult(
        name="has_findings",
        passed=isinstance(findings, list),
        expected="findings is a list",
        actual=str(type(findings).__name__),
    ))

    return checks


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_eval_run(results: list[EvalResult]) -> dict[str, Any]:
    """Compute aggregate scores across all eval cases."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    errored = sum(1 for r in results if r.error)
    avg_score = sum(r.score for r in results) / total if total else 0.0

    # Per-level breakdown
    by_level: dict[str, list[EvalResult]] = {}
    for r in results:
        # Extract level from case_id prefix
        level = r.case_id.split("-")[0]
        by_level.setdefault(level, []).append(r)

    level_scores = {}
    for level, level_results in by_level.items():
        level_scores[level] = {
            "total": len(level_results),
            "passed": sum(1 for r in level_results if r.passed),
            "avg_score": sum(r.score for r in level_results) / len(level_results),
        }

    return {
        "total": total,
        "passed": passed,
        "failed": total - passed - errored,
        "errored": errored,
        "avg_score": round(avg_score, 3),
        "pass_rate": round(passed / total, 3) if total else 0.0,
        "by_level": level_scores,
    }
