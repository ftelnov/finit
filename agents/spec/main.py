"""Spec Generator Agent - creates structured specifications from routed tasks."""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.base import BaseAgent
from common.llm import chat_json

SYSTEM_PROMPT = """You are a specification generator for software engineering tasks.
Given a task description and its classified domains, generate a structured specification.

Respond with JSON:
{
  "title": "short title for the task",
  "description": "detailed description of what needs to be done",
  "domains": ["domain1"],
  "acceptance_criteria": [
    {
      "id": "AC-1",
      "description": "what must be true",
      "test_strategy": "how to verify this criterion"
    }
  ],
  "files_to_create": ["path/to/file1.go"],
  "files_to_modify": ["existing/file.go"],
  "dependencies": ["any new dependencies needed"],
  "estimated_complexity": "low|medium|high"
}

Be specific and testable in acceptance criteria. Each criterion must be mechanically verifiable."""


class SpecAgent(BaseAgent):
    name = "spec"
    input_stream = "tasks:pending_spec"
    output_stream = "tasks:specced"

    def process(self, task: dict) -> dict:
        domains = task.get("domains", [])
        user_prompt = f"""Task: {task['input']}
Domains: {', '.join(domains)}

Generate a structured specification."""

        spec = chat_json(self.llm, SYSTEM_PROMPT, user_prompt, self.model)

        print(f"[spec] Generated spec: {spec.get('title', 'untitled')}")

        task["spec"] = spec
        task["status"] = "specced"
        return task


if __name__ == "__main__":
    agent = SpecAgent()
    agent.run()
