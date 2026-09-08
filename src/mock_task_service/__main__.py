"""Run the local mock service using explicit environment configuration."""

import uvicorn

from mock_task_service.config import Settings


def main() -> None:
    settings = Settings.from_environment()
    uvicorn.run(
        "mock_task_service.api:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
