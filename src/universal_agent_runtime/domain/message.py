"""API-visible conversation data, independent of native agent transcripts."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class Message:
    message_id: str
    turn_id: str
    sequence: int
    role: Literal["user", "assistant"]
    content: str = field(repr=False)
    created_at: datetime
