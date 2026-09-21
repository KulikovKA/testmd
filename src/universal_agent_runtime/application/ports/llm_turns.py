"""Bounded, read-only projection of committed native conversation turns."""

from dataclasses import dataclass, field

MAX_TURNS_PAGE = 20
MAX_TURN_TEXT = 8192
MAX_TURNS_RESPONSE_BYTES = 256 * 1024


class LLMTurnsFailure(Exception):
    def __init__(self, code: str):
        self.code = (
            code
            if code
            in {
                "llm_turns_unavailable",
                "llm_turns_busy",
                "llm_session_not_found",
                "llm_transcript_not_found",
                "llm_transcript_invalid",
                "llm_turns_limit",
            }
            else "llm_turns_unavailable"
        )
        super().__init__(self.code)


@dataclass(frozen=True)
class LLMTurn:
    number: int
    phase: str | None
    prompt: str = field(repr=False)
    assistant: str | None = field(repr=False)
    duration_ms: int | float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    tool_names: tuple[str, ...] = ()
    prompt_truncated: bool = False
    assistant_truncated: bool = False


@dataclass(frozen=True)
class LLMTurnsPage:
    model: str
    turns: tuple[LLMTurn, ...]
    next_after: int | None = None
