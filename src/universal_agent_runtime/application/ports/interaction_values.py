"""Runtime-neutral values for one logical Agent conversation."""

from dataclasses import dataclass

from universal_agent_runtime.domain.identifiers import AgentId, SessionId


@dataclass(frozen=True)
class SessionReference:
    """Project-owned identity; never a Qwen session ID or filesystem path."""

    agent_id: AgentId
    session_id: SessionId

    def __post_init__(self) -> None:
        if not isinstance(self.agent_id, AgentId) or not isinstance(
            self.session_id, SessionId
        ):
            raise TypeError("invalid Agent or Session identity")


@dataclass(frozen=True)
class TurnRequest:
    session: SessionReference
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.session, SessionReference):
            raise TypeError("invalid Session reference")
        if (
            not isinstance(self.message, str)
            or not self.message.strip()
            or "\x00" in self.message
            or len(self.message) > 16_384
        ):
            raise ValueError("message must contain 1..16384 safe text characters")


@dataclass(frozen=True)
class SessionObservation:
    session: SessionReference
    completed_turns: int

    def __post_init__(self) -> None:
        if not isinstance(self.session, SessionReference):
            raise TypeError("invalid Session reference")
        if type(self.completed_turns) is not int or self.completed_turns < 0:
            raise ValueError("completed_turns must be a non-negative integer")


@dataclass(frozen=True)
class TurnResult:
    session: SessionReference
    completed_turns: int
    response: str

    def __post_init__(self) -> None:
        SessionObservation(self.session, self.completed_turns)
        if not isinstance(self.response, str) or not self.response:
            raise ValueError("response must be non-empty text")


@dataclass(frozen=True)
class DeleteSessionResult:
    session: SessionReference

    def __post_init__(self) -> None:
        if not isinstance(self.session, SessionReference):
            raise TypeError("invalid Session reference")
