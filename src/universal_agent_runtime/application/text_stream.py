"""Transport-independent text filtering for provisional assistant output."""

import re
from collections.abc import Callable


def _ordered_secrets(secrets: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(set(filter(None, secrets)), key=len, reverse=True))


class StreamingRedactor:
    """Redact leftmost, longest secret matches without reprocessing replacements.

    Only a raw suffix that could complete a secret is retained. In particular,
    a complete short secret waits if it can still become a longer secret.
    """

    def __init__(self, secrets: tuple[str, ...], emit: Callable[[str], None]) -> None:
        self._secrets = _ordered_secrets(secrets)
        self._emit = emit
        self._pending = ""
        self.emitted = ""

    def push(self, text: str) -> None:
        self._pending += text
        self._flush(final=False)

    def finish(self) -> None:
        self._flush(final=True)

    def _flush(self, *, final: bool) -> None:
        parts: list[str] = []
        position = 0
        while position < len(self._pending):
            if not final and any(
                len(secret) > len(self._pending) - position
                and secret.startswith(self._pending[position:])
                for secret in self._secrets
            ):
                break
            match = next(
                (
                    secret
                    for secret in self._secrets
                    if self._pending.startswith(secret, position)
                ),
                None,
            )
            if match is not None:
                parts.append("[REDACTED]")
                position += len(match)
            else:
                parts.append(self._pending[position])
                position += 1
        self._pending = self._pending[position:]
        text = "".join(parts)
        if text:
            self.emitted += text
            self._emit(text)


def redact_text(text: str, secrets: tuple[str, ...]) -> str:
    """Use exactly the same matching rules for final and incremental output."""

    ordered = _ordered_secrets(secrets)
    if not ordered:
        return text
    # Batch transcripts can be megabytes: avoid a per-character Python scan.
    # Ordered alternatives use the same leftmost/longest, non-recursive matches.
    return re.sub("|".join(re.escape(secret) for secret in ordered), "[REDACTED]", text)


class TextDeltaCoalescer:
    """Combine small, already-redacted deltas by size, with no timer dependency."""

    def __init__(
        self, emit: Callable[[str], None], *, min_characters: int = 32
    ) -> None:
        if type(min_characters) is not int or min_characters < 1:
            raise ValueError("min_characters must be a positive integer")
        self._emit = emit
        self._minimum = min_characters
        self._pending = ""

    def push(self, text: str) -> None:
        self._pending += text
        if len(self._pending) >= self._minimum:
            self.finish()

    def finish(self) -> None:
        if self._pending:
            text, self._pending = self._pending, ""
            self._emit(text)
