"""Mock LLM server that returns structured responses for testing.

Implements the OpenAI-compatible /v1/chat/completions endpoint.
Returns deterministic JSON responses based on the system prompt content,
allowing full pipeline testing without real LLM credits.
"""

import json
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler


def make_router_response(user_msg: str) -> dict:
    domains = []
    lower = user_msg.lower()
    if any(w in lower for w in ["go ", "golang", "go-", "go service"]):
        domains.append("go-backend")
    if any(w in lower for w in ["python", "script", "fibonacci", "prime"]):
        domains.append("python")
    if any(w in lower for w in ["docker", "ci", "deploy", "devops"]):
        domains.append("devops")
    if any(w in lower for w in ["csv", "database", "sql", "db"]):
        domains.append("database")
    if not domains:
        domains = ["python"]
    return {
        "domains": domains,
        "reasoning": f"Classified based on keywords. Detected domains: {', '.join(domains)}",
    }


def make_spec_response(user_msg: str) -> dict:
    return {
        "title": "Implementation task",
        "description": f"Implement the requested functionality based on the task description.",
        "domains": ["python"],
        "acceptance_criteria": [
            {
                "id": "AC-1",
                "description": "Function is implemented correctly",
                "test_strategy": "Unit tests verify correct behavior",
            },
            {
                "id": "AC-2",
                "description": "Code follows language idioms",
                "test_strategy": "Code review for idiomatic patterns",
            },
        ],
        "files_to_create": ["solution.py", "test_solution.py"],
        "files_to_modify": [],
        "dependencies": [],
        "estimated_complexity": "low",
    }


def make_developer_response(user_msg: str) -> dict:
    return {
        "files": [
            {
                "path": "solution.py",
                "content": 'def is_prime(n: int) -> bool:\n    """Check if a number is prime."""\n    if n < 2:\n        return False\n    for i in range(2, int(n**0.5) + 1):\n        if n % i == 0:\n            return False\n    return True\n',
                "language": "python",
            },
            {
                "path": "test_solution.py",
                "content": 'from solution import is_prime\n\ndef test_primes():\n    assert is_prime(2)\n    assert is_prime(3)\n    assert is_prime(17)\n    assert not is_prime(1)\n    assert not is_prime(4)\n    assert not is_prime(0)\n',
                "language": "python",
            },
        ],
        "commands_run": ["python -m pytest test_solution.py -v"],
        "notes": "Implemented with standard trial division algorithm.",
    }


def make_reviewer_response(user_msg: str) -> dict:
    return {
        "verdict": "PASS",
        "score": 85,
        "findings": [
            {
                "severity": "info",
                "file": "solution.py",
                "description": "Implementation uses efficient trial division up to sqrt(n)",
                "evidence": "for i in range(2, int(n**0.5) + 1) - correct upper bound",
                "suggestion": "None needed",
            }
        ],
        "acceptance_criteria_results": [
            {
                "id": "AC-1",
                "status": "PASS",
                "evidence": "Function correctly identifies primes using trial division",
            },
            {
                "id": "AC-2",
                "status": "PASS",
                "evidence": "Code uses type hints, docstrings, and pythonic patterns",
            },
        ],
        "summary": "Implementation is correct, clean, and well-tested.",
    }


def detect_agent_type(system_prompt: str) -> str:
    lower = system_prompt.lower()
    if "semantic router" in lower or "classify" in lower:
        return "router"
    if "specification generator" in lower:
        return "spec"
    if "expert software developer" in lower:
        return "developer"
    if "strict code reviewer" in lower:
        return "reviewer"
    return "unknown"


AGENT_HANDLERS = {
    "router": make_router_response,
    "spec": make_spec_response,
    "developer": make_developer_response,
    "reviewer": make_reviewer_response,
}


class LLMHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path in ("/v1/chat/completions", "/chat/completions"):
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length))

            messages = body.get("messages", [])
            system_msg = ""
            user_msg = ""
            for m in messages:
                if m["role"] == "system":
                    system_msg = m["content"]
                elif m["role"] == "user":
                    user_msg = m["content"]

            agent_type = detect_agent_type(system_msg)
            handler = AGENT_HANDLERS.get(agent_type, make_router_response)
            result = handler(user_msg)

            response = {
                "id": f"mock-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.get("model", "mock-llm"),
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(result),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
            print(f"[mock-llm] {agent_type}: responded")

        elif self.path in ("/v1/models", "/models"):
            response = {
                "object": "list",
                "data": [{"id": "mock-llm", "object": "model", "created": int(time.time()), "owned_by": "finit"}],
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        elif self.path in ("/v1/models", "/models"):
            self.do_POST()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress default logging
        pass


if __name__ == "__main__":
    port = 8000
    server = HTTPServer(("0.0.0.0", port), LLMHandler)
    print(f"[mock-llm] Mock LLM server listening on :{port}")
    server.serve_forever()
