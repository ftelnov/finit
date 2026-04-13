"""Planner agent entry point."""

from __future__ import annotations

import logging
import uvicorn

from finit_agent.a2a import AgentCard, AgentCapabilities, AgentSkill, create_a2a_app
from finit_agent.telemetry import setup_telemetry, instrument_fastapi
from planner.agent import handle_task

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

AGENT_CARD = AgentCard(
    name="planner",
    description="Generates structured task specifications with acceptance criteria",
    url="http://planner:9000",
    version="1.0.0",
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[
        AgentSkill(
            id="create_spec",
            name="Create Specification",
            description="Generate a structured task specification from a task description",
        ),
    ],
)


def create_app():
    setup_telemetry("planner")
    app = create_a2a_app(AGENT_CARD, handle_task)
    instrument_fastapi(app)
    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "planner.main:app",
        host="0.0.0.0",
        port=9000,
        log_level="info",
    )
