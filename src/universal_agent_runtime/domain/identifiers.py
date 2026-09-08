"""Project-owned identities, never filesystem paths or backend identifiers."""

import re
from dataclasses import dataclass


def validate_identifier(value: str) -> None:
    """Reject ambiguous/path-like identities without reflecting untrusted input."""
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value
    ):
        raise ValueError(
            "identifier must be 1..64 ASCII letters, digits, hyphens or underscores"
        )


@dataclass(frozen=True)
class AgentId:
    value: str

    def __post_init__(self) -> None:
        validate_identifier(self.value)


@dataclass(frozen=True)
class WorkspaceId:
    value: str

    def __post_init__(self) -> None:
        validate_identifier(self.value)
