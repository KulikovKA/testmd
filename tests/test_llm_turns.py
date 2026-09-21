"""Native transcript fixtures and HTTP projections; no Qwen/network execution."""

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests import test_ag_ui as chat_http
from universal_agent_runtime.adapters.qwen_session import (
    QwenExecution,
    QwenSessionAdapter,
    QwenSessionConfig,
)
from universal_agent_runtime.composition import compose_application

SECRET = "observable-test-api-secret"
PRIVATE = "observable-hidden-reasoning"
CERT = "observable-certificate-content"


class Runner:
    def __init__(self):
        self.transcript = None
        self.counter = 0

    def run(self, invocation):
        self.counter += 1
        self.transcript = (
            invocation.qwen_home
            / "projects/workspace/chats"
            / f"{invocation.native_session_id}.jsonl"
        )
        self.transcript.parent.mkdir(parents=True, exist_ok=True)
        previous = (
            self.transcript.read_text(encoding="utf-8")
            if self.transcript.exists()
            else ""
        )
        events = [
            {
                "type": "system",
                "content": "SYSTEM_PROMPT_PRIVATE",
                "environment": {"OPENAI_API_KEY": SECRET},
            },
            {
                "type": "user",
                "message": {"role": "user", "parts": [{"text": invocation.prompt}]},
            },
            {
                "type": "assistant",
                "message": {
                    "parts": [
                        {
                            "functionCall": {
                                "name": "get_task",
                                "args": {"password": "tool-private"},
                            }
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "parts": [
                        {
                            "functionResponse": {
                                "name": "get_task",
                                "response": "tool-private",
                            }
                        }
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "model",
                    "parts": [
                        {"thought": True, "text": PRIVATE},
                        {"type": "reasoning", "text": PRIVATE},
                        {"text": f"Final answer {self.counter}: {SECRET} {CERT}"},
                    ],
                },
                "usageMetadata": {
                    "promptTokenCount": 120 * self.counter,
                    "candidatesTokenCount": 20,
                    "totalTokenCount": 120 * self.counter + 20,
                },
            },
            {
                "type": "system",
                "subtype": "ui_telemetry",
                "systemPayload": {
                    "uiEvent": {
                        "duration_ms": 1000 * self.counter,
                        "response_text": PRIVATE,
                    }
                },
            },
        ]
        self.transcript.write_text(
            previous + "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
        )
        return QwenExecution(
            invocation.native_session_id, f"Final answer {self.counter}"
        )

    def close(self):
        pass


class LLMTurnsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        certificate = self.root / "ca.pem"
        certificate.write_text(CERT, encoding="utf-8")
        old = chat_http.AGUIHttpTests()._app(self.root / "app").state.composition
        self.runner = Runner()
        self.adapter = QwenSessionAdapter(
            QwenSessionConfig(
                storage_root=self.root / "sessions",
                base_url="http://ollama.example/v1",
                model="test-model",
                api_key=SECRET,
                sfera_base_url="https://sfera.example",
                sfera_username="sfera-private-user",
                sfera_password="sfera-private-pass",
                sfera_ca_cert_path=certificate,
            ),
            runner=self.runner,
        )
        from universal_agent_runtime.http_api import create_application

        self.client = TestClient(
            create_application(
                compose_application(
                    old.settings, runtime=old.runtime, interaction=self.adapter
                )
            )
        )
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.agent = self.client.post(
            "/agents", json={"request_id": "llm-agent"}
        ).json()["agent_id"]
        self.assertEqual(
            self.client.post(f"/agents/{self.agent}/start").status_code, 200
        )
        self.endpoint = f"/agents/{self.agent}/llm-turns"

    def turn(self, message="DEVELOPMENT_PHASE: PLANNING\nPlan a Java library"):
        response = self.client.post(
            f"/agents/{self.agent}/messages", json={"content": message}
        )
        self.assertEqual(response.status_code, 201, response.text)

    def test_multiple_turns_telemetry_order_pagination_and_secret_projection(self):
        self.turn("DEVELOPMENT_PHASE: ANALYZING_REQUIREMENTS\nUse " + SECRET)
        self.turn()
        response = self.client.get(self.endpoint)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual([t["number"] for t in body["turns"]], [1, 2])
        self.assertEqual(
            [t["phase"] for t in body["turns"]], ["ANALYZING_REQUIREMENTS", "PLANNING"]
        )
        self.assertEqual(body["turns"][0]["duration_ms"], 1000)
        self.assertEqual(body["turns"][1]["input_tokens"], 240)
        self.assertEqual(body["turns"][1]["output_tokens"], 20)
        self.assertEqual(body["turns"][1]["total_tokens"], 260)
        self.assertEqual(body["turns"][1]["tool_names"], ["get_task"])
        for secret in (
            SECRET,
            PRIVATE,
            CERT,
            "SYSTEM_PROMPT_PRIVATE",
            "tool-private",
            "PAST_CONVERSATION_JSON",
            "CURRENT_USER_MESSAGE_TO_ANSWER_NOW",
        ):
            self.assertNotIn(secret, response.text)
        self.assertIn("Final answer 2", body["turns"][1]["assistant"])
        page = self.client.get(self.endpoint + "?limit=1").json()
        self.assertEqual(page["next_after"], 1)
        self.assertEqual(
            self.client.get(self.endpoint + "?after=1&limit=1").json()["turns"][0][
                "number"
            ],
            2,
        )
        self.assertEqual(
            self.client.get(self.endpoint + "?after=2").json()["turns"], []
        )

    def test_missing_transcript_session_empty_and_busy_are_explicit(self):
        self.assertEqual(self.client.get(self.endpoint).json()["turns"], [])
        self.turn()
        marker = self.root / "sessions" / self.agent / ".turn-in-progress"
        marker.write_text("pending", encoding="utf-8")
        self.assertEqual(
            self.client.get(self.endpoint).json()["error"]["code"], "llm_turns_busy"
        )
        marker.unlink()
        self.runner.transcript.unlink()
        self.assertEqual(
            self.client.get(self.endpoint).json()["error"]["code"],
            "llm_transcript_not_found",
        )
        (self.root / "sessions" / self.agent / "session-state.json").unlink()
        self.assertEqual(
            self.client.get(self.endpoint).json()["error"]["code"],
            "llm_session_not_found",
        )
        self.assertEqual(self.client.get("/agents/absent/llm-turns").status_code, 404)

    def test_malformed_untrusted_transcript_fails_closed(self):
        self.turn()
        original = self.runner.transcript.read_text(encoding="utf-8")
        for text in [
            "{malformed " + SECRET,
            "[]\n",
            '{"type":"assistant","message":"private"}\n',
            original + '{"type":"assistant","message":{"parts":"bad"}}\n',
            '{"type":"user","type":"assistant"}\n',
            original + '{"type":"result","usage":{"input_tokens":NaN}}\n',
            original + '{"type":"user","content":"uncommitted"}\n',
        ]:
            with self.subTest(text=text[:30]):
                self.runner.transcript.write_text(text, encoding="utf-8")
                response = self.client.get(self.endpoint)
                self.assertEqual(response.status_code, 502, response.text)
                self.assertEqual(
                    response.json()["error"]["code"], "llm_transcript_invalid"
                )
                self.assertNotIn(SECRET, response.text)

    def test_limits_and_no_reasoning_or_environment_in_text_parts(self):
        self.turn()
        original = self.runner.transcript.read_text(encoding="utf-8")
        for content in [
            "Authorization: Bearer arbitrary-private",
            "OPENAI_API_KEY=arbitrary-private",
            "<think>arbitrary-private</think>answer",
            '"environment": {"value":"arbitrary-private"}',
            "-----BEGIN CERTIFICATE-----\narbitrary-private\n-----END CERTIFICATE-----",
        ]:
            event = {"type": "assistant", "message": {"parts": [{"text": content}]}}
            self.runner.transcript.write_text(
                original + json.dumps(event) + "\n", encoding="utf-8"
            )
            response = self.client.get(self.endpoint)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("arbitrary-private", response.text)
        self.runner.transcript.write_text(
            original + json.dumps({"type": "assistant", "content": "a" * 12000}) + "\n",
            encoding="utf-8",
        )
        turn = self.client.get(self.endpoint).json()["turns"][0]
        self.assertTrue(turn["assistant_truncated"])
        self.assertEqual(len(turn["assistant"]), 8192)
        self.assertEqual(self.client.get(self.endpoint + "?limit=21").status_code, 422)
        self.runner.transcript.write_text(
            original
            + json.dumps({"type": "assistant", "content": "a" * 300000})
            + "\n",
            encoding="utf-8",
        )
        self.assertEqual(self.client.get(self.endpoint).status_code, 413)

    def test_read_is_read_only_and_unknown_metrics_are_null(self):
        self.turn("ordinary user message")
        events = [
            {"type": "user", "content": "ignored wrapper"},
            {
                "type": "assistant",
                "content": "visible answer",
                "extra": {"system_prompt": SECRET},
            },
            {"type": "assistant", "role": "system", "content": "SYSTEM_PROMPT_PRIVATE"},
            {"type": "assistant", "channel": "analysis", "content": PRIVATE},
        ]
        self.runner.transcript.write_text(
            "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
        )
        before = {
            str(p): p.read_bytes()
            for p in (self.root / "sessions").rglob("*")
            if p.is_file()
        }
        response = self.client.get(self.endpoint)
        self.assertEqual(response.status_code, 200)
        turn = response.json()["turns"][0]
        self.assertEqual(turn["prompt"], "ordinary user message")
        self.assertEqual(turn["assistant"], "visible answer")
        self.assertIsNone(turn["phase"])
        self.assertIsNone(turn["duration_ms"])
        self.assertIsNone(turn["input_tokens"])
        self.assertEqual(
            before,
            {
                str(p): p.read_bytes()
                for p in (self.root / "sessions").rglob("*")
                if p.is_file()
            },
        )

    def test_total_response_budget_paginates_and_large_transcript_rejects(self):
        for index in range(9):
            self.turn(f"message {index}")
        events = [
            json.loads(line)
            for line in self.runner.transcript.read_text(encoding="utf-8").splitlines()
        ]
        for event in events:
            if event["type"] == "assistant" and "usageMetadata" in event:
                event["message"]["parts"] = [{"text": "😀" * 8192}]
        self.runner.transcript.write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
            encoding="utf-8",
        )
        response = self.client.get(self.endpoint + "?limit=20")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.content), 256 * 1024)
        after = response.json()["next_after"]
        self.assertGreater(after, 0)
        self.assertLess(after, 9)
        following = self.client.get(self.endpoint + f"?after={after}&limit=20").json()
        self.assertEqual(following["turns"][0]["number"], after + 1)
        self.assertIsNone(following["next_after"])
        self.runner.transcript.write_text(
            '{"type":"system","padding":"' + "x" * (8 * 1024 * 1024) + '"}\n',
            encoding="utf-8",
        )
        self.assertEqual(self.client.get(self.endpoint).status_code, 413)

    def test_transcript_path_cannot_escape_and_unsupported_reader_is_explicit(self):
        self.turn()
        manifest = self.root / "sessions" / self.agent / "session-state.json"
        state = json.loads(manifest.read_text(encoding="utf-8"))
        state["transcript_path"] = f"../../{state['native_session_id']}.jsonl"
        manifest.write_text(json.dumps(state), encoding="utf-8")
        response = self.client.get(self.endpoint)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("sessions", response.text)
        with TestClient(
            chat_http.AGUIHttpTests()._app(self.root / "no-reader")
        ) as client:
            agent = client.post("/agents", json={"request_id": "no-reader"}).json()[
                "agent_id"
            ]
            response = client.get(f"/agents/{agent}/llm-turns")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "llm_turns_unavailable")
