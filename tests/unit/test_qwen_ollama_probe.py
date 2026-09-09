import json

import pytest

from qwen_ollama_probe.probe import (
    PROBE_MARKER,
    ProbeCategory,
    ProbeConfig,
    classify_http,
    classify_process,
    parse_stream,
)


def config(**changes: object) -> ProbeConfig:
    values: dict[str, object] = {
        "base_url": "http://host.docker.internal:11434/v1",
        "model": "qwen3:0.6b",
        "api_key": "ollama",
        "request_timeout_seconds": 300,
        "wall_time_seconds": 360,
        "max_tokens": 384,
        "max_session_turns": 6,
        "max_tool_calls": 2,
    }
    values.update(changes)
    return ProbeConfig(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field,value",
    [
        ("base_url", "localhost:11434/v1"),
        ("base_url", "http://user:pass@host/v1"),
        ("base_url", "http://host/api"),
        ("model", ""),
        ("model", "bad model"),
        ("api_key", ""),
        ("max_tokens", 63),
        ("max_session_turns", 1),
    ],
)
def test_invalid_configuration_is_rejected(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        config(**{field: value}).validate()


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (200, "{}", ProbeCategory.SUCCESS),
        (401, "unauthorized", ProbeCategory.OLLAMA_AUTHENTICATION),
        (403, "forbidden", ProbeCategory.OLLAMA_AUTHENTICATION),
        (404, "model not found", ProbeCategory.MODEL_UNAVAILABLE),
        (503, "unavailable", ProbeCategory.OLLAMA_CONNECTION),
    ],
)
def test_preflight_failures_are_classified(
    status: int, body: str, expected: ProbeCategory
) -> None:
    assert classify_http(status, body) is expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ECONNREFUSED", ProbeCategory.OLLAMA_CONNECTION),
        ("401 Unauthorized", ProbeCategory.OLLAMA_AUTHENTICATION),
        ("model x not found", ProbeCategory.MODEL_UNAVAILABLE),
        ("unexpected exit", ProbeCategory.QWEN_PROCESS),
    ],
)
def test_process_failures_are_classified(text: str, expected: ProbeCategory) -> None:
    assert classify_process(text) is expected


def test_stream_protocol_extracts_session_result_and_tool_call() -> None:
    lines = [
        {"type": "system", "session_id": "session-one"},
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "read_file"}]},
        },
        {
            "type": "result",
            "subtype": "success",
            "session_id": "session-one",
            "result": PROBE_MARKER,
        },
    ]
    outcome = parse_stream("\n".join(json.dumps(line) for line in lines), "test")
    assert outcome.category is ProbeCategory.SUCCESS
    assert outcome.session_id == "session-one"
    assert outcome.result == PROBE_MARKER
    assert outcome.tools == ("read_file",)


def test_non_json_or_missing_result_is_protocol_failure() -> None:
    assert parse_stream("noise", "test").category is ProbeCategory.QWEN_PROTOCOL
    assert (
        parse_stream('{"type":"system"}', "test").category
        is ProbeCategory.QWEN_PROTOCOL
    )


def test_provider_error_inside_success_event_is_not_success() -> None:
    event = {
        "type": "result",
        "subtype": "success",
        "session_id": "session-one",
        "result": "[API Error: 401 Incorrect API key]",
    }
    outcome = parse_stream(json.dumps(event), "test")
    assert outcome.category is ProbeCategory.OLLAMA_AUTHENTICATION
