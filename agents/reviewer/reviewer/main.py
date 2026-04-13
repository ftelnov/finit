"""Reviewer agent entry point."""

from __future__ import annotations

import logging
import uvicorn

from finit_agent.a2a import AgentCard, AgentCapabilities, AgentSkill, create_a2a_app
from finit_agent.telemetry import setup_telemetry, instrument_fastapi
from reviewer.agent import handle_task

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

AGENT_CARD = AgentCard(
    name="reviewer",
    description="Evaluates worker output against task specifications with evidence-based review",
    url="http://reviewer:9003",
    version="1.0.0",
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[
        AgentSkill(
            id="review",
            name="Review Code",
            description="Evaluate code artifacts against task specification",
        ),
    ],
)


def create_app():
    setup_telemetry("reviewer")
    app = create_a2a_app(AGENT_CARD, handle_task)
    instrument_fastapi(app)
    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "reviewer.main:app",
        host="0.0.0.0",
        port=9003,
        log_level="info",
    )
