"""Environment-only bind configuration for the local mock service."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8001

    @classmethod
    def from_environment(cls) -> "Settings":
        host = os.getenv("MOCK_TASK_API_HOST", cls.host)
        raw_port = os.getenv("MOCK_TASK_API_PORT", str(cls.port))
        if not host or any(character.isspace() for character in host):
            raise ValueError(
                "MOCK_TASK_API_HOST must be a non-empty host without whitespace"
            )
        try:
            port = int(raw_port)
        except ValueError:
            raise ValueError("MOCK_TASK_API_PORT must be an integer") from None
        if not 1 <= port <= 65535:
            raise ValueError("MOCK_TASK_API_PORT must be in 1..65535")
        return cls(host=host, port=port)
