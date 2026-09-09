"""Containerized, bounded Qwen Code/Ollama integration verification."""

import argparse
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import docker
from docker.errors import ContainerError, DockerException, ImageNotFound

QWEN_CODE_VERSION = "0.23.1"
QWEN_IMAGE = (
    "ghcr.io/qwenlm/qwen-code@"
    "sha256:996a12729e25f694254768ac8d3b5f870c54e6ac5825e169299e85a19c78cc10"
)
PROBE_MARKER = "QWEN_OLLAMA_PROBE_OK"
TOOL_MARKER = "TASK005_TOOL_FIXTURE"


class ProbeCategory(str, Enum):
    SUCCESS = "success"
    INVALID_CONFIGURATION = "invalid_configuration"
    OLLAMA_CONNECTION = "ollama_connection"
    OLLAMA_AUTHENTICATION = "ollama_authentication"
    MODEL_UNAVAILABLE = "model_unavailable"
    QWEN_PROCESS = "qwen_process"
    QWEN_PROTOCOL = "qwen_protocol"
    EXPECTATION_FAILED = "expectation_failed"


@dataclass(frozen=True)
class ProbeConfig:
    base_url: str
    model: str
    api_key: str
    request_timeout_seconds: int
    wall_time_seconds: int
    max_tokens: int
    max_session_turns: int
    max_tool_calls: int

    @classmethod
    def from_environment(cls) -> "ProbeConfig":
        return cls(
            os.getenv(
                "QWEN_OLLAMA_BASE_URL",
                "http://host.docker.internal:11434/v1",
            ),
            os.getenv("QWEN_OLLAMA_MODEL", "qwen3:0.6b"),
            os.getenv("QWEN_OLLAMA_API_KEY", "ollama"),
            _integer_env("QWEN_OLLAMA_REQUEST_TIMEOUT_SECONDS", 300),
            _integer_env("QWEN_OLLAMA_WALL_TIME_SECONDS", 360),
            _integer_env("QWEN_OLLAMA_MAX_TOKENS", 384, minimum=64),
            _integer_env("QWEN_OLLAMA_MAX_SESSION_TURNS", 6),
            _integer_env("QWEN_OLLAMA_MAX_TOOL_CALLS", 2, minimum=0),
        )

    def validate(self) -> None:
        parsed = urlparse(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/v1"
        ):
            raise ValueError("QWEN_OLLAMA_BASE_URL must be an explicit /v1 HTTP(S) URL")
        if not self.model or any(character.isspace() for character in self.model):
            raise ValueError("QWEN_OLLAMA_MODEL must be a non-empty model identifier")
        if not self.api_key or "\x00" in self.api_key:
            raise ValueError("QWEN_OLLAMA_API_KEY is invalid")
        if self.request_timeout_seconds < 1:
            raise ValueError("QWEN_OLLAMA_REQUEST_TIMEOUT_SECONDS must be at least 1")
        if self.wall_time_seconds < 1:
            raise ValueError("QWEN_OLLAMA_WALL_TIME_SECONDS must be at least 1")
        if self.max_tokens < 64:
            raise ValueError("QWEN_OLLAMA_MAX_TOKENS must be at least 64")
        if self.max_session_turns < 2:
            raise ValueError("QWEN_OLLAMA_MAX_SESSION_TURNS must be at least 2")
        if self.max_tool_calls < 0:
            raise ValueError("QWEN_OLLAMA_MAX_TOOL_CALLS must not be negative")


@dataclass(frozen=True)
class ProbeOutcome:
    category: ProbeCategory
    mode: str
    detail: str
    session_id: str | None = None
    result: str | None = None
    tools: tuple[str, ...] = ()


def _integer_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def classify_http(status: int, body: str) -> ProbeCategory:
    lowered = body.lower()
    if status in {401, 403}:
        return ProbeCategory.OLLAMA_AUTHENTICATION
    if status == 404 or ("model" in lowered and "not found" in lowered):
        return ProbeCategory.MODEL_UNAVAILABLE
    if not 200 <= status < 300:
        return ProbeCategory.OLLAMA_CONNECTION
    return ProbeCategory.SUCCESS


def classify_process(text: str) -> ProbeCategory:
    lowered = text.lower()
    if any(
        marker in lowered
        for marker in ("econnrefused", "fetch failed", "connection error")
    ):
        return ProbeCategory.OLLAMA_CONNECTION
    if "401" in lowered or "403" in lowered or "unauthorized" in lowered:
        return ProbeCategory.OLLAMA_AUTHENTICATION
    if "model" in lowered and any(
        marker in lowered for marker in ("not found", "does not exist")
    ):
        return ProbeCategory.MODEL_UNAVAILABLE
    return ProbeCategory.QWEN_PROCESS


def parse_stream(text: str, mode: str) -> ProbeOutcome:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    if not events:
        return ProbeOutcome(ProbeCategory.QWEN_PROTOCOL, mode, "no JSONL events")
    session_id = next(
        (str(event["session_id"]) for event in events if event.get("session_id")),
        None,
    )
    final = next(
        (
            event
            for event in reversed(events)
            if event.get("type") == "result" and event.get("subtype") == "success"
        ),
        None,
    )
    if final is None:
        return ProbeOutcome(
            ProbeCategory.QWEN_PROTOCOL,
            mode,
            "missing successful result event",
            session_id,
        )
    tools = tuple(sorted(_tool_names(events)))
    result = str(final.get("result", ""))
    if result.lstrip().startswith("[API Error:"):
        return ProbeOutcome(
            classify_process(result),
            mode,
            "provider error reported in a successful result event",
            session_id,
            result,
            tools,
        )
    return ProbeOutcome(
        ProbeCategory.SUCCESS, mode, "completed", session_id, result, tools
    )


def _tool_names(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        if value.get("type") in {"tool_use", "tool_call"} and isinstance(
            value.get("name"), str
        ):
            found.add(value["name"])
        for child in value.values():
            found.update(_tool_names(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_tool_names(child))
    return found


class QwenOllamaProbe:
    def __init__(self, config: ProbeConfig) -> None:
        config.validate()
        self.config = config
        self._temporary = tempfile.TemporaryDirectory(prefix="qwen-ollama-probe-")
        self._root = Path(self._temporary.name)
        self._home = self._root / "qwen-home"
        self._workspace = self._root / "workspace"
        self._home.mkdir()
        self._workspace.mkdir()
        self._session_token = ""
        (self._workspace / "probe.txt").write_text(TOOL_MARKER, encoding="utf-8")
        self._write_settings()
        self._client = docker.from_env(timeout=config.wall_time_seconds + 30)

    def close(self) -> None:
        self._client.close()
        self._temporary.cleanup()

    def _write_settings(self) -> None:
        settings = {
            "modelProviders": {
                "openai": [
                    {
                        "id": self.config.model,
                        "name": "TASK-005 Ollama probe",
                        "envKey": "OLLAMA_API_KEY",
                        "baseUrl": self.config.base_url,
                        "generationConfig": {
                            "timeout": self.config.request_timeout_seconds * 1000,
                            "maxRetries": 0,
                            "contextWindowSize": 32768,
                            "samplingParams": {
                                "temperature": 0,
                                "max_tokens": self.config.max_tokens,
                            },
                            "extra_body": {"reasoning_effort": "low"},
                        },
                    }
                ]
            },
            "security": {"auth": {"selectedType": "openai"}},
            "model": {"name": self.config.model},
            "telemetry": {"enabled": False},
            "general": {"chatRecording": True},
        }
        (self._home / "settings.json").write_text(
            json.dumps(settings, indent=2) + "\n", encoding="utf-8"
        )

    def _volumes(self) -> dict[str, dict[str, str]]:
        return {
            str(self._home): {"bind": "/root/.qwen", "mode": "rw"},
            str(self._workspace): {"bind": "/workspace", "mode": "ro"},
        }

    def preflight(self) -> ProbeOutcome:
        script = r"""
const base = process.env.PROBE_BASE_URL.replace(/\/$/, '');
const model = encodeURIComponent(process.env.PROBE_MODEL);
fetch(`${base}/models/${model}`, {headers: {Authorization: `Bearer ${process.env.OLLAMA_API_KEY}`}})
  .then(async response => { console.log(JSON.stringify({status: response.status, body: await response.text()})); })
  .catch(error => { console.error(error.message); process.exit(70); });
"""
        try:
            output = self._client.containers.run(
                QWEN_IMAGE,
                ["node", "-e", script],
                environment={
                    "PROBE_BASE_URL": self.config.base_url,
                    "PROBE_MODEL": self.config.model,
                    "OLLAMA_API_KEY": self.config.api_key,
                },
                remove=True,
            ).decode("utf-8", errors="replace")
        except (DockerException, ContainerError) as error:
            return ProbeOutcome(
                ProbeCategory.OLLAMA_CONNECTION,
                "preflight",
                type(error).__name__,
            )
        try:
            payload = json.loads(output.splitlines()[-1])
            status = int(payload["status"])
            body = str(payload["body"])
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return ProbeOutcome(
                ProbeCategory.QWEN_PROTOCOL, "preflight", "invalid preflight output"
            )
        category = classify_http(status, body)
        return ProbeOutcome(category, "preflight", f"HTTP {status}")

    def run(self, mode: str, prompt: str, *extra: str) -> ProbeOutcome:
        command = [
            "qwen",
            "--bare",
            "--auth-type",
            "openai",
            "--openai-base-url",
            self.config.base_url,
            "--model",
            self.config.model,
            "--output-format",
            "stream-json",
            "--max-session-turns",
            str(self.config.max_session_turns),
            "--max-tool-calls",
            str(self.config.max_tool_calls),
            "--max-wall-time",
            f"{self.config.wall_time_seconds}s",
            *extra,
            "-p",
            prompt,
        ]
        container = None
        try:
            container = self._client.containers.run(
                QWEN_IMAGE,
                command,
                environment={
                    "OLLAMA_API_KEY": self.config.api_key,
                    "OPENAI_API_KEY": self.config.api_key,
                },
                volumes=self._volumes(),
                working_dir="/workspace",
                detach=True,
            )
            wait_result = container.wait(timeout=self.config.wall_time_seconds + 30)
            output = container.logs(stdout=True, stderr=True).decode(
                "utf-8", errors="replace"
            )
            exit_status = int(wait_result["StatusCode"])
            if exit_status != 0:
                return ProbeOutcome(
                    classify_process(output),
                    mode,
                    f"Qwen exited with status {exit_status}",
                )
        except ContainerError as error:
            return ProbeOutcome(
                ProbeCategory.QWEN_PROCESS,
                mode,
                f"Qwen container failed with status {error.exit_status}",
            )
        except (ImageNotFound, DockerException) as error:
            return ProbeOutcome(ProbeCategory.QWEN_PROCESS, mode, type(error).__name__)
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except DockerException:
                    pass
        return parse_stream(output, mode)

    def run_prompt(self) -> ProbeOutcome:
        token = f"SESSION_TOKEN_{uuid4().hex[:12]}"
        outcome = self.run(
            "prompt",
            f"/think Remember {token}. Reply exactly {PROBE_MARKER}",
            "--session-id",
            str(uuid4()),
            "--exclude-tools",
            "read_file,edit,notebook_edit,run_shell_command,get_goal,update_goal",
        )
        if outcome.category is ProbeCategory.SUCCESS and PROBE_MARKER not in (
            outcome.result or ""
        ):
            return ProbeOutcome(
                ProbeCategory.EXPECTATION_FAILED,
                outcome.mode,
                "expected prompt marker absent",
                outcome.session_id,
                outcome.result,
                outcome.tools,
            )
        self._session_token = token
        return outcome

    def run_resume(self, session_id: str) -> ProbeOutcome:
        token = self._session_token
        outcome = self.run(
            "resume",
            "What SESSION_TOKEN did I ask you to remember? Reply with only that token.",
            "--resume",
            session_id,
            "--exclude-tools",
            "read_file,edit,notebook_edit,run_shell_command,get_goal,update_goal",
        )
        if outcome.category is ProbeCategory.SUCCESS and token not in (
            outcome.result or ""
        ):
            return ProbeOutcome(
                ProbeCategory.EXPECTATION_FAILED,
                outcome.mode,
                "resumed answer did not contain the session token",
                outcome.session_id,
                outcome.result,
                outcome.tools,
            )
        return outcome

    def run_tool(self) -> ProbeOutcome:
        outcome = self.run(
            "tool",
            '/think Call read_file exactly once with absolute_path "/workspace/probe.txt". '
            f"Then reply exactly TOOL_PROBE_OK:{TOOL_MARKER}",
            "--approval-mode",
            "yolo",
            "--allowed-tools",
            "read_file",
            "--exclude-tools",
            "edit,notebook_edit,run_shell_command,get_goal,update_goal",
        )
        if (
            outcome.category is ProbeCategory.SUCCESS
            and "read_file" not in outcome.tools
        ):
            return ProbeOutcome(
                ProbeCategory.EXPECTATION_FAILED,
                outcome.mode,
                "read_file tool call absent",
                outcome.session_id,
                outcome.result,
                outcome.tools,
            )
        return outcome


def _serialize(outcome: ProbeOutcome) -> dict[str, Any]:
    value = asdict(outcome)
    value["category"] = outcome.category.value
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prompt", "tool", "session", "all"))
    args = parser.parse_args(argv)
    try:
        config = ProbeConfig.from_environment()
        probe = QwenOllamaProbe(config)
    except ValueError as error:
        print(
            json.dumps(
                _serialize(
                    ProbeOutcome(
                        ProbeCategory.INVALID_CONFIGURATION, args.mode, str(error)
                    )
                )
            )
        )
        return 2
    try:
        outcomes = [probe.preflight()]
        if outcomes[0].category is ProbeCategory.SUCCESS:
            prompt: ProbeOutcome | None = None
            if args.mode in {"prompt", "session", "all"}:
                prompt = probe.run_prompt()
                outcomes.append(prompt)
            if (
                args.mode in {"session", "all"}
                and prompt is not None
                and prompt.category is ProbeCategory.SUCCESS
                and prompt.session_id
            ):
                outcomes.append(probe.run_resume(prompt.session_id))
            if args.mode in {"tool", "all"}:
                outcomes.append(probe.run_tool())
        print(
            json.dumps(
                [_serialize(outcome) for outcome in outcomes], ensure_ascii=False
            )
        )
        return (
            0
            if all(outcome.category is ProbeCategory.SUCCESS for outcome in outcomes)
            else 1
        )
    finally:
        probe.close()
