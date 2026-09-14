from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from universal_agent_runtime.adapters.qwen_session import _extract_mcp_metrics


_SCRIPT = Path(__file__).parents[1] / "scripts" / "benchmark_agent.py"
_SPEC = importlib.util.spec_from_file_location("benchmark_agent", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark)


class BenchmarkClientTests(unittest.TestCase):
    def test_nearest_rank_percentile_and_aggregate(self) -> None:
        runs = [
            {"total_run_ms": 2.0, "ttft_ms": 1.0},
            {"total_run_ms": 4.0, "ttft_ms": None},
            {"total_run_ms": 7.0, "ttft_ms": 3.0},
        ]
        self.assertEqual(benchmark._percentile_nearest_rank([2.0, 4.0, 7.0], 0.95), 7.0)
        self.assertEqual(benchmark._aggregate(runs, "total_run_ms")["median_ms"], 4.0)
        self.assertEqual(benchmark._aggregate(runs, "ttft_ms")["p95_ms"], 3.0)

    def test_scenario_guardrails_prevent_unapproved_mutations(self) -> None:
        with self.assertRaisesRegex(ValueError, "--entity is required"):
            benchmark._scenario_configuration("read", entity=None, area=None)
        with self.assertRaisesRegex(ValueError, "--entity and --area"):
            benchmark._scenario_configuration("epic-decomposition", entity="TTEST2-1", area=None)
        with self.assertRaisesRegex(ValueError, "--allow-mutations"):
            benchmark.run_once("http://127.0.0.1:1", "epic-decomposition", "TTEST2-1", "TTEST2", False)

    def test_mcp_metric_parser_strips_only_valid_metric_records(self) -> None:
        output, metrics = _extract_mcp_metrics(
            'ordinary stderr\nUAR_METRIC {"kind":"mcp_tool","tool_name":"get_task","duration_ms":1,"success":true}\nUAR_METRIC malformed\n'
        )
        self.assertEqual(output, "ordinary stderr\nUAR_METRIC malformed")
        self.assertEqual(metrics[0]["tool_name"], "get_task")


if __name__ == "__main__":
    unittest.main()
