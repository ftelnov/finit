"""Worker agent entry point."""

from __future__ import annotations

import logging
import uvicorn

from finit_agent.a2a import AgentCard, AgentCapabilities, AgentSkill, create_a2a_app
from finit_agent.telemetry import setup_telemetry, instrument_fastapi
from worker.agent import handle_task

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

AGENT_CARD = AgentCard(
    name="worker",
    description="Generates code and tests within workspace sandbox",
    url="http://worker:9002",
    version="1.0.0",
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[
        AgentSkill(
            id="develop",
            name="Develop Code",
            description="Generate code and tests based on task specification",
        ),
    ],
)


def create_app():
    setup_telemetry("worker")
    app = create_a2a_app(AGENT_CARD, handle_task)
    instrument_fastapi(app)
    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "worker.main:app",
        host="0.0.0.0",
        port=9002,
        log_level="info",
    )
