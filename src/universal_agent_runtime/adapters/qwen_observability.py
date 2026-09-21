"""Strict native JSONL projection. System/tool payloads never become debug text."""

import json
import re
from dataclasses import asdict

from universal_agent_runtime.application.observability_text import observable_text
from universal_agent_runtime.application.ports.llm_turns import (
    MAX_TURN_TEXT,
    MAX_TURNS_PAGE,
    MAX_TURNS_RESPONSE_BYTES,
    LLMTurn,
    LLMTurnsFailure,
    LLMTurnsPage,
)
from universal_agent_runtime.domain.development_task import DevelopmentState

MAX_TRANSCRIPT_EVENTS = 20000
MAX_TRANSCRIPT_LINE_BYTES = 256 * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("non-finite number")


def _parts(event):
    message = event.get("message")
    if message is not None and not isinstance(message, dict):
        raise ValueError("invalid message")
    if isinstance(message, dict):
        if "parts" in message:
            value = message["parts"]
            if not isinstance(value, list) or not all(
                isinstance(part, dict) for part in value
            ):
                raise ValueError("invalid parts")
            return value
        value = message.get("content")
    else:
        value = event.get("content")
    if value is None:
        return []
    if isinstance(value, str):
        return [{"text": value}]
    if not isinstance(value, list) or not all(isinstance(part, dict) for part in value):
        raise ValueError("invalid content")
    return value


def _visible_assistant(event):
    parts = _parts(event)
    message = event.get("message") or {}
    if event.get("role") not in (None, "assistant", "model"):
        return None
    if event.get("channel") not in (None, "final") or message.get("channel") not in (
        None,
        "final",
    ):
        return None
    if event.get("subtype") in {"reasoning", "thinking", "analysis"}:
        return None
    if any(
        event.get(key) or message.get(key)
        for key in ("thought", "reasoning", "thinking", "thoughtSignature")
    ):
        return None
    if message.get("role") not in (None, "assistant", "model"):
        return None
    content = []
    for part in parts:
        # Only plain text parts. All reasoning/function/data structures are dropped.
        if (
            set(part) - {"text", "type", "thought"}
            or part.get("thought") is not None
            and part.get("thought") is not False
        ):
            continue
        if part.get("type") not in (None, "text", "output_text"):
            continue
        if "text" in part:
            if not isinstance(part["text"], str):
                raise TypeError("invalid assistant text")
            content.append(part["text"])
    return "".join(content) if content else None


def project_turns(
    raw: bytes,
    history: list[tuple[str, str]],
    *,
    model: str,
    secrets: tuple[str, ...],
    metrics_reader,
    after: int,
    limit: int,
) -> LLMTurnsPage:
    if (
        type(after) is not int
        or after < 0
        or type(limit) is not int
        or not 1 <= limit <= MAX_TURNS_PAGE
    ):
        raise LLMTurnsFailure("llm_turns_limit")
    try:
        lines = raw.decode("utf-8").splitlines()
        if len(lines) > MAX_TRANSCRIPT_EVENTS or any(
            len(line.encode()) > MAX_TRANSCRIPT_LINE_BYTES for line in lines
        ):
            raise LLMTurnsFailure("llm_turns_limit")
        groups = []
        for line in lines:
            event = json.loads(
                line, object_pairs_hook=_unique, parse_constant=_invalid_constant
            )
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise TypeError("invalid event")
            kind = event["type"]
            if kind == "user":
                parts = _parts(event)
                if not any(
                    "functionResponse" in part or part.get("type") == "tool_result"
                    for part in parts
                ):
                    groups.append([])
            elif kind == "assistant":
                _visible_assistant(
                    event
                )  # Validate even turns outside the requested page.
            if groups:
                groups[-1].append((line, event))
        if len(groups) != len(history):
            raise ValueError("uncommitted or mismatched transcript")
        turns = []
        model = observable_text(model, secrets)[:256]
        for number in range(after + 1, min(len(groups), after + limit) + 1):
            group = groups[number - 1]
            # History stores the actual current message, never Qwen's full
            # system/skills/past-conversation prompt wrapper.
            prompt = history[number - 1][0]
            match = re.match(r"\ADEVELOPMENT_PHASE: ([A-Z_]+)\n", prompt)
            phase = (
                match[1]
                if match and match[1] in {s.value for s in DevelopmentState}
                else None
            )
            assistant = None
            for _, event in group:
                if event["type"] == "assistant":
                    visible = _visible_assistant(event)
                    if visible is not None:
                        assistant = visible
            _, metrics, tool_names = metrics_reader([line for line, _ in group])
            prompt = observable_text(prompt, secrets)
            assistant = (
                observable_text(assistant, secrets) if assistant is not None else None
            )
            turn = LLMTurn(
                number,
                phase,
                prompt[:MAX_TURN_TEXT],
                assistant[:MAX_TURN_TEXT] if assistant is not None else None,
                metrics.get("duration_ms"),
                metrics.get("input_tokens"),
                metrics.get("output_tokens"),
                metrics.get("total_tokens"),
                tuple(
                    name
                    for name in tool_names
                    if observable_text(name, secrets) == name
                ),
                len(prompt) > MAX_TURN_TEXT,
                assistant is not None and len(assistant) > MAX_TURN_TEXT,
            )
            candidate = LLMTurnsPage(model, (*turns, turn), number)
            if (
                len(json.dumps(asdict(candidate), ensure_ascii=False).encode())
                > MAX_TURNS_RESPONSE_BYTES - 1024
            ):
                if not turns:
                    raise LLMTurnsFailure("llm_turns_limit")
                break
            turns.append(turn)
        last = turns[-1].number if turns else after
        return LLMTurnsPage(model, tuple(turns), last if last < len(groups) else None)
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise LLMTurnsFailure("llm_transcript_invalid") from None
