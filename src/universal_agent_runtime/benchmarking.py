"""Opt-in, privacy-preserving benchmark telemetry helpers."""

import json
import logging
import sys
from typing import Any


_LOGGER = logging.getLogger("universal_agent_runtime.benchmark")
_SAFE_FIELDS = frozenset(
    {
        "agent_id",
        "turn_id",
        "duration_ms",
        "agent_application_ms",
        "qwen_total_ms",
        "qwen_execution_ms",
        "backend_inference_requests_observable",
        "backend_inference_requests",
        "stream_json_event_types",
        "mcp_calls_total",
        "mcp_total_ms",
        "mcp_calls_by_tool",
        "tool_name",
        "route",
        "method",
        "status_code",
        "status_class",
        "success",
    }
)
_LOGGER.setLevel(logging.INFO)
_LOGGER.propagate = False
if not any(getattr(handler, "_uar_benchmark_handler", False) for handler in _LOGGER.handlers):
    _HANDLER = logging.StreamHandler(sys.stderr)
    _HANDLER._uar_benchmark_handler = True  # type: ignore[attr-defined]
    _HANDLER.setLevel(logging.INFO)
    _HANDLER.setFormatter(logging.Formatter("%(message)s"))
    _LOGGER.addHandler(_HANDLER)


def emit_benchmark_metric(enabled: bool, kind: str, **values: Any) -> None:
    """Write one structured metric without accepting user or service payloads."""

    if not enabled:
        return
    safe_values = {key: value for key, value in values.items() if key in _SAFE_FIELDS}
    _LOGGER.info(
        json.dumps(
            {"event": "uar_benchmark", "kind": kind, **safe_values},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
