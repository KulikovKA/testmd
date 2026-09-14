from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from universal_agent_runtime.adapters.qwen_session import (
    QwenInvocation,
    _benchmark_environment,
    _emit_mcp_metrics,
    _emit_qwen_metric,
    _extract_mcp_metrics,
)


_SCRIPT = Path(__file__).parents[1] / "scripts" / "benchmark_agent.py"
_SPEC = importlib.util.spec_from_file_location("benchmark_agent", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark)


class BenchmarkClientTests(unittest.TestCase):
    def test_nearest_rank_percentile_and_aggregate(self) -> None:
        runs = [
            {"iteration_total_ms": 2.0, "time_to_first_text_ms": 1.0},
            {"iteration_total_ms": 4.0, "time_to_first_text_ms": None},
            {"iteration_total_ms": 7.0, "time_to_first_text_ms": 3.0},
        ]
        self.assertEqual(benchmark._percentile_nearest_rank([2.0, 4.0, 7.0], 0.95), 7.0)
        aggregate = benchmark._aggregate(runs, "iteration_total_ms")
        assert aggregate is not None
        self.assertEqual(aggregate["median_ms"], 4.0)
        self.assertEqual(aggregate["max_ms"], 7.0)
        self.assertEqual(
            benchmark._aggregate(runs, "time_to_first_text_ms")["p95_ms"], 3.0
        )

    def test_epic_baseline_creates_a_new_epic_without_entity_or_task_count(self) -> None:
        skills, tools, prompt = benchmark._scenario_configuration(
            "epic-decomposition", entity=None, area="TTEST2"
        )
        self.assertEqual(skills, ["task-decomposition"])
        self.assertEqual(
            tools, ["get_task", "create_task", "create_epic", "add_child_task"]
        )
        self.assertEqual(
            prompt,
            "Создай Epic в TTEST2 для AI-агента, который проверяет технические "
            "требования на полноту, непротиворечивость и тестируемость, и "
            "декомпозируй его на задачи.",
        )
        self.assertNotIn("Read", prompt)
        self.assertNotIn("one", prompt)

    def test_scenario_guardrails_prevent_unapproved_mutations(self) -> None:
        with self.assertRaisesRegex(ValueError, "--entity is required"):
            benchmark._scenario_configuration("read", entity=None, area=None)
        with self.assertRaisesRegex(ValueError, "--area is required"):
            benchmark._scenario_configuration("epic-decomposition", entity=None, area=None)
        with self.assertRaisesRegex(ValueError, "--allow-mutations"):
            benchmark.run_once("http://127.0.0.1:1", "epic-decomposition", None, "TTEST2", False)

    def test_run_timings_separate_agent_start_from_ag_ui_and_save_identifiers(self) -> None:
        requests: list[tuple[str, str]] = []

        def fake_request(_base: str, method: str, path: str, _body: object = None) -> tuple[int, bytes]:
            requests.append((method, path))
            if path == "/agents":
                return 201, b'{"agent_id":"agent-benchmark"}'
            return 200 if method == "POST" else 204, b""

        observed_starts: list[int] = []

        def fake_stream(
            _base: str, agent_id: str, payload: dict[str, object], started_ns: int
        ) -> tuple[int, dict[str, object]]:
            observed_starts.append(started_ns)
            self.assertEqual(agent_id, "agent-benchmark")
            self.assertIn("threadId", payload)
            self.assertIn("runId", payload)
            return 200, {
                "time_to_run_started_ms": 1.0,
                "time_to_first_text_ms": 2.0,
                "time_to_run_finished_ms": 3.0,
                "heartbeats": 0,
                "success": True,
                "error": False,
                "response_characters": 5,
            }

        with (
            patch.object(benchmark, "_request", side_effect=fake_request),
            patch.object(benchmark, "_run_stream", side_effect=fake_stream),
            patch.object(
                benchmark.time,
                "perf_counter_ns",
                side_effect=[
                    100_000_000,
                    200_000_000,
                    201_000_000,
                    1_300_000_000,
                    2_300_000_000,
                    2_400_000_000,
                ],
            ),
        ):
            result = benchmark.run_once(
                "http://example.invalid", "chat", None, None, False
            )
        self.assertEqual(requests[1], ("POST", "/agents/agent-benchmark/start"))
        self.assertEqual(requests[-1], ("DELETE", "/agents/agent-benchmark"))
        self.assertTrue(observed_starts)
        self.assertEqual(observed_starts, [1_300_000_000])
        self.assertEqual(result["agent_start_ms"], 1.0)
        self.assertEqual(result["time_to_first_text_ms"], 2.0)
        self.assertIn("agent_start_ms", result)
        self.assertIn("ag_ui_total_ms", result)
        self.assertIn("iteration_total_ms", result)
        self.assertEqual(result["agent_id"], "agent-benchmark")
        self.assertEqual(len(result["thread_id"]), 32)
        self.assertEqual(len(result["run_id"]), 32)

    def test_sse_timing_uses_the_ag_ui_timer_only(self) -> None:
        lines = [
            b'data: {"type":"RUN_STARTED"}\n',
            b'data: {"type":"TEXT_MESSAGE_CONTENT","delta":"ok"}\n',
            b'data: {"type":"RUN_FINISHED"}\n',
        ]
        with patch.object(
            benchmark.time,
            "perf_counter_ns",
            side_effect=[10_000_000, 20_000_000, 30_000_000],
        ):
            metrics = benchmark._sse_metrics(lines, 0)
        self.assertEqual(metrics["time_to_run_started_ms"], 10.0)
        self.assertEqual(metrics["time_to_first_text_ms"], 20.0)
        self.assertEqual(metrics["time_to_run_finished_ms"], 30.0)

    def test_mcp_metric_parser_strips_only_valid_metric_records(self) -> None:
        output, metrics = _extract_mcp_metrics(
            'ordinary stderr\nUAR_METRIC {"kind":"mcp_tool","tool_name":"get_task","duration_ms":1,"success":true}\nUAR_METRIC malformed\n'
        )
        self.assertEqual(output, "ordinary stderr\nUAR_METRIC malformed")
        self.assertEqual(metrics[0]["tool_name"], "get_task")

    def test_server_metrics_have_agent_and_turn_correlation(self) -> None:
        invocation = QwenInvocation(
            Path("qwen-home"),
            Path("workspace"),
            uuid4(),
            "prompt",
            False,
            benchmark_agent_id="agent-one",
            benchmark_turn_id="native-session:1",
        )
        self.assertEqual(
            _benchmark_environment(invocation),
            {
                "UAR_BENCHMARK_AGENT_ID": "agent-one",
                "UAR_BENCHMARK_TURN_ID": "native-session:1",
            },
        )
        with patch(
            "universal_agent_runtime.adapters.qwen_session.emit_benchmark_metric"
        ) as emit:
            _emit_mcp_metrics(
                True,
                (
                    {
                        "kind": "mcp_tool",
                        "tool_name": "get_task",
                        "duration_ms": 1.0,
                        "success": True,
                    },
                ),
                invocation,
            )
            _emit_qwen_metric(True, 1, None, invocation=invocation)
        fields = [call.kwargs for call in emit.call_args_list]
        self.assertTrue(all(value["agent_id"] == "agent-one" for value in fields))
        self.assertTrue(all(value["turn_id"] == "native-session:1" for value in fields))


if __name__ == "__main__":
    unittest.main()
