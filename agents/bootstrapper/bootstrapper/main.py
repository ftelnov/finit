"""Bootstrapper agent entry point."""

from __future__ import annotations

import logging
import uvicorn

from finit_agent.a2a import AgentCard, AgentCapabilities, AgentSkill, create_a2a_app
from finit_agent.telemetry import setup_telemetry, instrument_fastapi
from bootstrapper.agent import handle_task

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

AGENT_CARD = AgentCard(
    name="bootstrapper",
    description="Analyzes task specifications and reports workspace capabilities",
    url="http://bootstrapper:9001",
    version="1.0.0",
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[
        AgentSkill(
            id="prepare_workspace",
            name="Prepare Workspace",
            description="Analyze spec and determine workspace capabilities",
        ),
        AgentSkill(
            id="extend_workspace",
            name="Extend Workspace",
            description="Add tools, dependencies, or MCP servers to an existing workspace",
        ),
    ],
)


def create_app():
    setup_telemetry("bootstrapper")
    app = create_a2a_app(AGENT_CARD, handle_task)
    instrument_fastapi(app)
    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "bootstrapper.main:app",
        host="0.0.0.0",
        port=9001,
        log_level="info",
    )
