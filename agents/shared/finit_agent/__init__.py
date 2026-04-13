"""Finit Agent - shared A2A framework for Finit platform agents."""

from finit_agent.a2a import create_a2a_app, A2AResult, AgentCard, AgentSkill
from finit_agent.llm import LLMClient
from finit_agent.llm import load_prompt
from finit_agent.llm import load_prompt_versioned
from finit_agent.schemas import (
    BootstrapResult,
    InstallPackage,
    ListFiles,
    PlannerSpec,
    ReadFile,
    RegisterMcpServer,
    ReviewVerdict,
    RunCommand,
    WebSearch,
    WriteFile,
)
from finit_agent.telemetry import setup_telemetry

__all__ = [
    "create_a2a_app",
    "A2AResult",
    "AgentCard",
    "AgentSkill",
    "BootstrapResult",
    "InstallPackage",
    "ListFiles",
    "LLMClient",
    "load_prompt",
    "load_prompt_versioned",
    "PlannerSpec",
    "ReadFile",
    "RegisterMcpServer",
    "ReviewVerdict",
    "RunCommand",
    "setup_telemetry",
    "WebSearch",
    "WriteFile",
]
