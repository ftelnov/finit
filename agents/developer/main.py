"""Developer Agent - generates code based on specification."""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.base import BaseAgent
from common.llm import chat_json

SYSTEM_PROMPT = """You are an expert software developer. Given a specification, generate the code
that fulfills all acceptance criteria.

Respond with JSON:
{
  "files": [
    {
      "path": "relative/path/to/file",
      "content": "full file content",
      "language": "go|python|yaml|dockerfile|etc"
    }
  ],
  "commands_run": ["list of commands that would be run to verify"],
  "notes": "any implementation notes"
}

Write clean, production-quality code. Include tests where the spec requires them.
Follow idiomatic patterns for each language."""


class DeveloperAgent(BaseAgent):
    name = "developer"
    input_stream = "tasks:pending_dev"
    output_stream = "tasks:developed"

    def process(self, task: dict) -> dict:
        spec = task.get("spec", {})
        if isinstance(spec, str):
            spec = json.loads(spec)

        user_prompt = f"""Implement the following specification:

Title: {spec.get('title', 'N/A')}
Description: {spec.get('description', 'N/A')}
Domains: {', '.join(spec.get('domains', task.get('domains', [])))}

Acceptance Criteria:
{json.dumps(spec.get('acceptance_criteria', []), indent=2)}

Files to create: {spec.get('files_to_create', [])}
Files to modify: {spec.get('files_to_modify', [])}
Dependencies: {spec.get('dependencies', [])}

Generate the complete implementation."""

        code = chat_json(self.llm, SYSTEM_PROMPT, user_prompt, self.model)

        files = code.get("files", [])
        print(f"[developer] Generated {len(files)} file(s)")
        for f in files:
            print(f"  - {f.get('path', '?')} ({f.get('language', '?')})")

        task["code"] = code
        task["status"] = "developed"
        return task


if __name__ == "__main__":
    agent = DeveloperAgent()
    agent.run()
