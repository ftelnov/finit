"""Base agent class with common setup."""

import os
from .bus import get_redis, consume_stream, publish
from .llm import get_llm_client, get_model


class BaseAgent:
    """Base class for all Finit agents."""

    name: str = "base"
    input_stream: str = ""
    output_stream: str = ""

    def __init__(self):
        self.rdb = get_redis()
        self.llm = get_llm_client()
        self.model = get_model()

    def process(self, task: dict) -> dict:
        """Process a task and return the updated task dict.

        Subclasses must implement this.
        """
        raise NotImplementedError

    def run(self):
        """Start consuming from input stream."""
        consumer = f"{self.name}-1"
        group = f"{self.name}-group"

        def handler(task: dict):
            result = self.process(task)
            if result and self.output_stream:
                publish(self.rdb, self.output_stream, result)
            return result

        consume_stream(
            self.rdb,
            self.input_stream,
            group,
            consumer,
            handler,
        )
