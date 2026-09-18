"""Read-only Agent report across the HTTP, lifecycle, and Qwen storage boundaries."""

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_runtime_driver import _DockerClient, _environment
from universal_agent_runtime.adapters.docker_runtime import (
    DockerRuntime,
    DockerWorkload,
)
from universal_agent_runtime.adapters.qwen_session import (
    QwenExecution,
    QwenInvocation,
    QwenSessionAdapter,
    QwenSessionConfig,
)
from universal_agent_runtime.composition import compose_application
from universal_agent_runtime.configuration import ApplicationSettings
from universal_agent_runtime.http_api import create_application

SECRET = "private-api-key-value"
COOKIE = "private-session-cookie-value"
CERTIFICATE = "private-ca-certificate-contents"


class _TranscriptRunner:
    def __init__(self) -> None:
        self.transcript: Path | None = None

    def run(self, invocation: QwenInvocation) -> QwenExecution:
        self.transcript = (
            invocation.qwen_home
            / "projects"
            / "workspace"
            / "chats"
            / f"{invocation.native_session_id}.jsonl"
        )
        self.transcript.parent.mkdir(parents=True, exist_ok=True)
        events = [
            {"type": "user", "content": SECRET},
            {"type": "system", "environment": {"OPENAI_API_KEY": SECRET}},
            {"type": "system", "cookie": COOKIE},
            {
                "type": "tool_call",
                "tool_name": "get_task",
                "arguments": {"key": SECRET},
            },
            {"type": "tool_result", "content": COOKIE},
            {"type": "assistant", "content": CERTIFICATE},
            {
                "type": "result",
                "duration_ms": 82157,
                "ttft_ms": 14981,
                "usage": {
                    "input_tokens": 1214,
                    "output_tokens": 488,
                    "thoughts_tokens": 0,
                    "total_tokens": 1702,
                },
            },
        ]
        self.transcript.write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
        )
        return QwenExecution(invocation.native_session_id, "Safe answer")

    def close(self) -> None:
        pass


class AgentDebugReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        ca_path = self.root / "sfera-ca.pem"
        ca_path.write_text(CERTIFICATE, encoding="utf-8")
        settings = ApplicationSettings.from_environment(
            {
                **_environment("docker"),
                "UAR_DOCKER_NETWORK_MODE": "none",
                "UAR_QWEN_SESSION_STORAGE_ROOT": str(self.root / "sessions"),
                "UAR_SKILL_REGISTRY_ROOT": str(self.root / "skills"),
            }
        )
        runtime = DockerRuntime(
            {
                "qwen-agent-image": DockerWorkload(
                    "uar-agent:0.1.0", ("serve",), "10001:10001"
                )
            },
            secret_resolver=lambda _: SECRET,
            client=_DockerClient(),
        )
        self.runner = _TranscriptRunner()
        adapter = QwenSessionAdapter(
            QwenSessionConfig(
                storage_root=self.root / "sessions",
                base_url="http://ollama.example/v1",
                model="test-model",
                api_key=SECRET,
                sfera_base_url="https://sfera.example",
                sfera_username="private-sfera-username",
                sfera_password="private-sfera-password",
                sfera_ca_cert_path=ca_path,
            ),
            runner=self.runner,
        )
        self.client = TestClient(
            create_application(
                compose_application(settings, runtime=runtime, interaction=adapter)
            )
        )
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def _create(self, *, skills: list[str] | None = None) -> str:
        response = self.client.post(
            "/agents", json={"request_id": "debug-agent", "skills": skills or []}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["agent_id"]

    def _start(self, agent_id: str) -> None:
        response = self.client.post(f"/agents/{agent_id}/start")
        self.assertEqual(response.status_code, 200, response.text)

    def _turn(self, agent_id: str) -> None:
        response = self.client.post(
            f"/agents/{agent_id}/messages", json={"content": "Please answer"}
        )
        self.assertEqual(response.status_code, 201, response.text)

    def _report(self, agent_id: str) -> dict:
        response = self.client.get(f"/agents/{agent_id}/debug-report")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_agent_before_first_turn(self) -> None:
        agent_id = self._create()
        before_start = self._report(agent_id)
        self.assertEqual(before_start["qwen"]["completed_turns"], 0)
        self.assertIsNone(before_start["last_turn"])
        self._start(agent_id)
        report = self._report(agent_id)
        self.assertEqual(report["agent"]["state"], "READY")
        self.assertEqual(report["qwen"]["completed_turns"], 0)
        self.assertFalse(report["qwen"]["transcript_present"])
        self.assertIsNone(report["last_turn"])
        self.assertTrue(report["workspace"]["mcp_server_present"])
        self.assertIsNone(report["failure"])

    def test_completed_turn_skills_telemetry_and_secret_exclusion(self) -> None:
        agent_id = self._create(skills=["code-testing"])
        self._start(agent_id)
        self._turn(agent_id)
        report = self._report(agent_id)
        self.assertEqual(report["configuration"]["skills"], ["code-testing"])
        self.assertEqual(report["workspace"]["skills"], ["code-testing"])
        self.assertEqual(report["qwen"]["model"], "test-model")
        self.assertEqual(report["qwen"]["completed_turns"], 1)
        self.assertTrue(report["qwen"]["transcript_present"])
        self.assertTrue(report["last_turn"]["user_message_present"])
        self.assertTrue(report["last_turn"]["assistant_message_present"])
        self.assertEqual(
            report["last_turn"]["qwen_events"],
            {"user": 1, "system": 2, "assistant": 1, "tool_call": 1, "tool_result": 1},
        )
        self.assertEqual(report["last_turn"]["mcp_tool_names"], ["get_task"])
        self.assertEqual(
            report["last_turn"]["telemetry"],
            {
                "duration_ms": 82157,
                "ttft_ms": 14981,
                "input_tokens": 1214,
                "output_tokens": 488,
                "thoughts_tokens": 0,
                "total_tokens": 1702,
            },
        )
        serialized = json.dumps(report)
        for secret in (
            SECRET,
            COOKIE,
            CERTIFICATE,
            "private-sfera-username",
            "private-sfera-password",
        ):
            self.assertNotIn(secret, serialized)

    def test_missing_and_corrupt_transcript_degrade_safely(self) -> None:
        agent_id = self._create()
        self._start(agent_id)
        self._turn(agent_id)
        assert self.runner.transcript is not None
        self.runner.transcript.unlink()
        missing = self._report(agent_id)
        self.assertEqual(missing["qwen"]["completed_turns"], 1)
        self.assertFalse(missing["qwen"]["transcript_present"])
        self.assertIsNone(missing["last_turn"]["telemetry"]["duration_ms"])
        self.runner.transcript.write_text("{malformed " + SECRET, encoding="utf-8")
        corrupt = self._report(agent_id)
        self.assertFalse(corrupt["qwen"]["transcript_present"])
        self.assertNotIn(SECRET, json.dumps(corrupt))

    def test_native_qwen_transcript_shape_extracts_telemetry(self) -> None:
        agent_id = self._create()
        self._start(agent_id)
        self._turn(agent_id)
        assert self.runner.transcript is not None
        events = [
            {"type": "user", "message": {"parts": [{"text": SECRET}]}},
            {"type": "system", "subtype": "attribution_snapshot"},
            {
                "type": "system",
                "subtype": "ui_telemetry",
                "systemPayload": {
                    "uiEvent": {
                        "duration_ms": 82157,
                        "ttft_ms": 14981,
                        "input_token_count": 1214,
                        "output_token_count": 488,
                        "thoughts_token_count": 0,
                        "total_token_count": 1702,
                        "response_text": SECRET,
                    }
                },
            },
            {
                "type": "assistant",
                "message": {
                    "parts": [
                        {"functionCall": {"name": "get_task", "args": {"key": SECRET}}}
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "parts": [
                        {"functionResponse": {"name": "get_task", "response": COOKIE}}
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {"parts": [{"text": CERTIFICATE}]},
                "usageMetadata": {
                    "promptTokenCount": 1214,
                    "candidatesTokenCount": 488,
                    "thoughtsTokenCount": 0,
                    "totalTokenCount": 1702,
                },
            },
        ]
        self.runner.transcript.write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
        )
        report = self._report(agent_id)
        self.assertEqual(report["last_turn"]["qwen_events"]["user"], 1)
        self.assertEqual(report["last_turn"]["qwen_events"]["system"], 2)
        self.assertEqual(report["last_turn"]["qwen_events"]["tool_call"], 1)
        self.assertEqual(report["last_turn"]["qwen_events"]["tool_result"], 1)
        self.assertEqual(report["last_turn"]["mcp_tool_names"], ["get_task"])
        self.assertEqual(report["last_turn"]["telemetry"]["duration_ms"], 82157)
        self.assertEqual(report["last_turn"]["telemetry"]["input_tokens"], 1214)
        for secret in (SECRET, COOKIE, CERTIFICATE):
            self.assertNotIn(secret, json.dumps(report))

    def test_session_state_cannot_redirect_transcript_outside_qwen_home(self) -> None:
        agent_id = self._create()
        self._start(agent_id)
        self._turn(agent_id)
        state_path = self.root / "sessions" / agent_id / "session-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["transcript_path"] = f"../../{state['native_session_id']}.jsonl"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        report = self._report(agent_id)
        self.assertFalse(report["qwen"]["transcript_present"])
        self.assertIsNone(report["last_turn"]["telemetry"]["duration_ms"])

    def test_unknown_agent_uses_existing_safe_404(self) -> None:
        response = self.client.get("/agents/absent/debug-report")
        inspect = self.client.get("/agents/absent")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), inspect.json())
