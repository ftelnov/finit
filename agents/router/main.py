"""Router Agent - classifies task into domains using LLM."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.base import BaseAgent
from common.llm import chat_json

SYSTEM_PROMPT = """You are a semantic router for software engineering tasks.
Given a task description, classify it into one or more domains.

Available domains:
- go-backend: Go backend development, APIs, services
- python: Python development
- devops: Docker, CI/CD, infrastructure
- frontend: Web frontend development
- database: Database schema, queries, migrations
- testing: Test writing, test infrastructure

Respond with JSON:
{
  "domains": ["domain1", "domain2"],
  "reasoning": "brief explanation of classification"
}

Always return at least one domain. Be precise."""


class RouterAgent(BaseAgent):
    name = "router"
    input_stream = "tasks:pending_routing"
    output_stream = "tasks:routed"

    def process(self, task: dict) -> dict:
        user_prompt = f"Classify this task:\n\n{task['input']}"

        result = chat_json(self.llm, SYSTEM_PROMPT, user_prompt, self.model)

        domains = result.get("domains", ["go-backend"])
        reasoning = result.get("reasoning", "")

        print(f"[router] Domains: {domains} | Reasoning: {reasoning}")

        task["domains"] = domains
        task["status"] = "routed"
        return task


if __name__ == "__main__":
    agent = RouterAgent()
    agent.run()
