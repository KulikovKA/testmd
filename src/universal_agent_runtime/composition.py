"""Inert composition entry point for the project scaffold."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ApplicationComposition:
    """A configuration-free marker returned without starting infrastructure."""

    package_name: str = "universal_agent_runtime"


def create_composition() -> ApplicationComposition:
    """Build the inert composition root without starting an API or runtime."""
    return ApplicationComposition()
