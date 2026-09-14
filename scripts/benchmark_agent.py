#!/usr/bin/env python3
"""Run repeatable, privacy-preserving benchmarks through the public AG-UI API."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SCENARIOS = ("chat", "read", "epic-decomposition")


def _request(base_url: str, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, bytes]:
    data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
    request = Request(
        f"{base_url.rstrip('/')}{path}", data=data, method=method,
        headers={"content-type": "application/json"} if data is not None else {},
    )
    try:
        with urlopen(request, timeout=960) as response:  # noqa: S310 - configured deployment origin
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()
    except URLError as error:
        raise RuntimeError("benchmark HTTP request failed") from error


def _percentile_nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _aggregate(runs: list[dict[str, Any]], field: str) -> dict[str, float] | None:
    values = [run[field] for run in runs if isinstance(run.get(field), (int, float))]
    if not values:
        return None
    return {
        "min_ms": min(values),
        "median_ms": _percentile_nearest_rank(values, 0.5),
        "mean_ms": round(sum(values) / len(values), 3),
        "p95_ms": _percentile_nearest_rank(values, 0.95),
        "max_ms": max(values),
    }


def _scenario_configuration(scenario: str, *, entity: str | None, area: str | None) -> tuple[list[str], list[str], str]:
    if scenario == "chat":
        return [], [], "Respond with one concise sentence confirming that you are ready."
    if scenario == "read":
        if not entity:
            raise ValueError("--entity is required for the read scenario")
        return ["task-decomposition"], ["get_task"], f"Read task {entity} and provide a concise summary."
    if not area:
        raise ValueError("--area is required for epic-decomposition")
    return (
        ["task-decomposition"],
        ["get_task", "create_task", "create_epic", "add_child_task"],
        (
            f"Создай Epic в {area} для AI-агента, который проверяет технические "
            "требования на полноту, непротиворечивость и тестируемость, и "
            "декомпозируй его на задачи."
        ),
    )


def _sse_metrics(lines: Any, started_ns: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "time_to_run_started_ms": None,
        "time_to_first_text_ms": None,
        "time_to_run_finished_ms": None,
        "heartbeats": 0, "success": False, "error": False, "response_characters": 0,
    }
    for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        now_ms = round((time.perf_counter_ns() - started_ns) / 1_000_000, 3)
        if line.startswith(":"):
            result["heartbeats"] += 1
            continue
        if not line.startswith("data: "):
            continue
        try:
            event = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "RUN_STARTED" and result["time_to_run_started_ms"] is None:
            result["time_to_run_started_ms"] = now_ms
        elif event_type == "TEXT_MESSAGE_CONTENT" and result["time_to_first_text_ms"] is None:
            result["time_to_first_text_ms"] = now_ms
            delta = event.get("delta")
            result["response_characters"] = len(delta) if isinstance(delta, str) else 0
        elif event_type == "RUN_FINISHED":
            result["time_to_run_finished_ms"] = now_ms
            result["success"] = True
        elif event_type == "RUN_ERROR":
            result["time_to_run_finished_ms"] = now_ms
            result["error"] = True
    return result


def _run_stream(base_url: str, agent_id: str, payload: dict[str, Any], started_ns: int) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload, separators=(",", ":")).encode()
    request = Request(
        f"{base_url.rstrip('/')}/ag-ui/agents/{agent_id}/run", data=data, method="POST",
        headers={"content-type": "application/json", "accept": "text/event-stream"},
    )
    try:
        with urlopen(request, timeout=960) as response:  # noqa: S310 - configured deployment origin
            return response.status, _sse_metrics(response, started_ns)
    except HTTPError as error:
        return error.code, _sse_metrics(error, started_ns)
    except URLError as error:
        raise RuntimeError("benchmark AG-UI request failed") from error


def run_once(base_url: str, scenario: str, entity: str | None, area: str | None, allow_mutations: bool) -> dict[str, Any]:
    skills, tools, prompt = _scenario_configuration(scenario, entity=entity, area=area)
    if scenario == "epic-decomposition" and not allow_mutations:
        raise ValueError("epic-decomposition requires --allow-mutations")
    request_id = f"benchmark-{uuid.uuid4().hex}"
    status, payload = _request(base_url, "POST", "/agents", {"request_id": request_id, "skills": skills, "tools": tools})
    if status not in {200, 201}:
        raise RuntimeError("Agent creation failed")
    agent_id = json.loads(payload).get("agent_id")
    if not isinstance(agent_id, str):
        raise RuntimeError("Agent creation returned an invalid response")
    iteration_started_ns = time.perf_counter_ns()
    try:
        agent_start_started_ns = time.perf_counter_ns()
        status, _ = _request(base_url, "POST", f"/agents/{agent_id}/start")
        agent_start_ms = round(
            (time.perf_counter_ns() - agent_start_started_ns) / 1_000_000, 3
        )
        if status != 200:
            raise RuntimeError("Agent start failed")
        thread_id, run_id = uuid.uuid4().hex, uuid.uuid4().hex
        ag_ui_started_ns = time.perf_counter_ns()
        status, metrics = _run_stream(base_url, agent_id, {
            "threadId": thread_id, "runId": run_id, "state": {},
            "messages": [{"role": "user", "content": prompt}], "tools": [],
            "context": [], "forwardedProps": {},
        }, ag_ui_started_ns)
        metrics["http_status"] = status
        metrics.update({
            "agent_id": agent_id,
            "thread_id": thread_id,
            "run_id": run_id,
            "agent_start_ms": agent_start_ms,
            "ag_ui_total_ms": round(
                (time.perf_counter_ns() - ag_ui_started_ns) / 1_000_000, 3
            ),
            "iteration_total_ms": round(
                (time.perf_counter_ns() - iteration_started_ns) / 1_000_000, 3
            ),
        })
        return metrics
    finally:
        _request(base_url, "DELETE", f"/agents/{agent_id}")


def _display_ms(value: object) -> str:
    return "-" if not isinstance(value, (int, float)) else f"{value:.1f}"


def _print_summary(runs: list[dict[str, Any]]) -> None:
    print("iteration success agent_start TTFT ag_ui_total iteration_total", file=sys.stderr)
    for index, run in enumerate(runs, start=1):
        print(
            f"{index:9d} {str(run['success']):7s} "
            f"{_display_ms(run.get('agent_start_ms')):11s} "
            f"{_display_ms(run.get('time_to_first_text_ms')):4s} "
            f"{_display_ms(run.get('ag_ui_total_ms')):11s} "
            f"{_display_ms(run.get('iteration_total_ms')):15s}",
            file=sys.stderr,
        )
    print("aggregate min median mean p95 max", file=sys.stderr)
    for label, field in (
        ("agent_start", "agent_start_ms"),
        ("TTFT", "time_to_first_text_ms"),
        ("ag_ui_total", "ag_ui_total_ms"),
        ("iteration_total", "iteration_total_ms"),
    ):
        aggregate = _aggregate(runs, field)
        if aggregate is not None:
            print(
                f"{label:15s} {aggregate['min_ms']:.1f} {aggregate['median_ms']:.1f} "
                f"{aggregate['mean_ms']:.1f} {aggregate['p95_ms']:.1f} {aggregate['max_ms']:.1f}",
                file=sys.stderr,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--entity")
    parser.add_argument("--area")
    parser.add_argument("--allow-mutations", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    try:
        runs = [run_once(args.base_url, args.scenario, args.entity, args.area, args.allow_mutations) for _ in range(args.repeats)]
    except (ValueError, RuntimeError) as error:
        parser.error(str(error))
    report = {
        "timestamp": datetime.now(UTC).isoformat(), "scenario": args.scenario,
        "repetitions": len(runs), "runs": runs,
        "aggregate": {
            "agent_start_ms": _aggregate(runs, "agent_start_ms"),
            "ag_ui_total_ms": _aggregate(runs, "ag_ui_total_ms"),
            "time_to_run_started_ms": _aggregate(runs, "time_to_run_started_ms"),
            "time_to_first_text_ms": _aggregate(runs, "time_to_first_text_ms"),
            "time_to_run_finished_ms": _aggregate(runs, "time_to_run_finished_ms"),
            "iteration_total_ms": _aggregate(runs, "iteration_total_ms"),
        },
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    _print_summary(runs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
