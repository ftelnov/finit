"""Review Agent - evidence-based code review against specification."""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.base import BaseAgent
from common.llm import chat_json

SYSTEM_PROMPT = """You are a strict code reviewer. You review code against a specification.
Every finding MUST be backed by evidence: either a specific code reference or a logical proof.
Do NOT make vague claims like "this might have issues". Be specific.

Respond with JSON:
{
  "verdict": "PASS|FAIL",
  "score": 0-100,
  "findings": [
    {
      "severity": "critical|major|minor|info",
      "file": "path/to/file",
      "description": "what the issue is",
      "evidence": "specific code snippet or logical reasoning",
      "suggestion": "how to fix it"
    }
  ],
  "acceptance_criteria_results": [
    {
      "id": "AC-1",
      "status": "PASS|FAIL",
      "evidence": "why this criterion is met or not met"
    }
  ],
  "summary": "overall assessment"
}

Rules:
1. Every finding must have concrete evidence (code reference or logical proof).
2. Do not penalize for things not in the spec.
3. PASS means all acceptance criteria are met and no critical issues found.
4. Be fair - good code should pass."""


class ReviewerAgent(BaseAgent):
    name = "reviewer"
    input_stream = "tasks:pending_review"
    output_stream = "tasks:reviewed"

    def process(self, task: dict) -> dict:
        spec = task.get("spec", {})
        code = task.get("code", {})

        if isinstance(spec, str):
            spec = json.loads(spec)
        if isinstance(code, str):
            code = json.loads(code)

        files_summary = ""
        for f in code.get("files", []):
            files_summary += f"\n--- {f.get('path', '?')} ({f.get('language', '?')}) ---\n"
            files_summary += f.get("content", "")[:3000]
            files_summary += "\n"

        user_prompt = f"""Review this implementation against the specification.

SPECIFICATION:
Title: {spec.get('title', 'N/A')}
Description: {spec.get('description', 'N/A')}

Acceptance Criteria:
{json.dumps(spec.get('acceptance_criteria', []), indent=2)}

IMPLEMENTATION:
{files_summary}

Implementation notes: {code.get('notes', 'None')}

Provide your evidence-based review."""

        review = chat_json(self.llm, SYSTEM_PROMPT, user_prompt, self.model)

        verdict = review.get("verdict", "UNKNOWN")
        score = review.get("score", 0)
        print(f"[reviewer] Verdict: {verdict} | Score: {score}")

        task["review"] = review
        task["status"] = "completed"
        return task


if __name__ == "__main__":
    agent = ReviewerAgent()
    agent.run()
