"""Opt-in, privacy-preserving benchmark telemetry helpers."""

import json
import logging
from typing import Any


_LOGGER = logging.getLogger(__name__)


def emit_benchmark_metric(enabled: bool, kind: str, **values: Any) -> None:
    """Write one structured metric without accepting user or service payloads."""

    if not enabled:
        return
    _LOGGER.info(
        "%s",
        json.dumps(
            {"event": "uar_benchmark", "kind": kind, **values},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
