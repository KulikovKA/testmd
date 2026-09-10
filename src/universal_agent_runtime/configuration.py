"""Validated deployment configuration for the API composition root."""

import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse


class ConfigurationError(ValueError):
    """Raised before application startup when deployment configuration is invalid."""


class RuntimeDriver(str, Enum):
    """Runtime drivers currently selectable by the composition root."""

    DOCKER = "docker"


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"missing required configuration: {name}")
    return value.strip()


def _port(environment: Mapping[str, str], name: str) -> int:
    raw = _required(environment, name)
    try:
        value = int(raw)
    except ValueError:
        raise ConfigurationError(f"{name} must be an integer in 1..65535") from None
    if not 1 <= value <= 65535:
        raise ConfigurationError(f"{name} must be an integer in 1..65535")
    return value


def _positive_float(environment: Mapping[str, str], name: str) -> float:
    raw = _required(environment, name)
    try:
        value = float(raw)
    except ValueError:
        raise ConfigurationError(f"{name} must be finite and positive") from None
    if not math.isfinite(value) or value <= 0:
        raise ConfigurationError(f"{name} must be finite and positive")
    return value


def _positive_int(environment: Mapping[str, str], name: str) -> int:
    raw = _required(environment, name)
    try:
        value = int(raw)
    except ValueError:
        raise ConfigurationError(f"{name} must be a positive integer") from None
    if value <= 0:
        raise ConfigurationError(f"{name} must be a positive integer")
    return value


def _identifier(environment: Mapping[str, str], name: str) -> str:
    value = _required(environment, name)
    if len(value) > 64 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise ConfigurationError(f"{name} must be a valid identifier")
    return value


def _command(environment: Mapping[str, str]) -> tuple[str, ...]:
    raw = _required(environment, "UAR_DOCKER_WORKLOAD_COMMAND_JSON")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        raise ConfigurationError(
            "UAR_DOCKER_WORKLOAD_COMMAND_JSON must be a JSON string array"
        ) from None
    if (
        not isinstance(decoded, list)
        or not decoded
        or not all(
            isinstance(item, str) and item and "\x00" not in item for item in decoded
        )
    ):
        raise ConfigurationError(
            "UAR_DOCKER_WORKLOAD_COMMAND_JSON must be a non-empty JSON string array"
        )
    return tuple(decoded)


def _optional_task_endpoint(environment: Mapping[str, str]) -> str | None:
    value = environment.get("UAR_TASK_API_BASE_URL", "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError("UAR_TASK_API_BASE_URL must be an HTTP(S) origin")
    return value.rstrip("/")


@dataclass(frozen=True)
class ApplicationSettings:
    """Explicit non-secret deployment inputs needed to assemble the local PoC."""

    api_host: str
    api_port: int
    runtime_driver: RuntimeDriver
    docker_workload_key: str
    docker_image: str
    docker_command: tuple[str, ...]
    docker_user: str
    docker_workspace_target: str
    docker_network_mode: str
    docker_network_host: str | None
    docker_network_port: int | None
    docker_healthcheck_interval_seconds: float
    docker_healthcheck_timeout_seconds: float
    docker_healthcheck_retries: int
    docker_healthcheck_start_period_seconds: float
    agent_cpu_cores: float
    agent_memory_bytes: int
    agent_operation_timeout_seconds: float
    agent_readiness_timeout_seconds: float
    agent_readiness_poll_interval_seconds: float
    qwen_storage_root: Path
    qwen_base_url: str
    qwen_model: str
    qwen_api_key_secret_id: str
    qwen_api_key: str = field(repr=False)
    task_api_base_url: str | None = None
    task_api_token_secret_id: str | None = None
    task_api_token: str | None = field(default=None, repr=False)
    task_api_timeout_seconds: float = 10.0
    task_api_max_response_bytes: int = 65_536
    chat_max_message_characters: int = 16_384
    chat_max_response_characters: int = 16_384
    chat_max_history_messages: int = 100
    chat_max_history_page_size: int = 50
    stream_heartbeat_seconds: float = 15.0
    stream_send_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        for value in (self.stream_heartbeat_seconds, self.stream_send_timeout_seconds):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ConfigurationError("stream timings must be finite and positive")
        for value in (
            self.chat_max_message_characters,
            self.chat_max_response_characters,
            self.chat_max_history_messages,
            self.chat_max_history_page_size,
        ):
            if type(value) is not int or value < 1:
                raise ConfigurationError("chat limits must be positive integers")
        if (
            self.chat_max_message_characters > 16_384
            or self.chat_max_history_messages < 2
        ):
            raise ConfigurationError("invalid chat limits")
        if self.task_api_base_url is not None:
            _optional_task_endpoint({"UAR_TASK_API_BASE_URL": self.task_api_base_url})
        if (self.task_api_token_secret_id is None) != (self.task_api_token is None):
            raise ConfigurationError(
                "Task API token ID and value must be configured together"
            )
        if self.task_api_token_secret_id is not None:
            _identifier(
                {"UAR_TASK_API_TOKEN_SECRET_ID": self.task_api_token_secret_id},
                "UAR_TASK_API_TOKEN_SECRET_ID",
            )
            if not self.task_api_token or "\x00" in self.task_api_token:
                raise ConfigurationError("UAR_TASK_API_TOKEN is invalid")
        if (
            type(self.task_api_max_response_bytes) is not int
            or not 1_024 <= self.task_api_max_response_bytes <= 1_048_576
        ):
            raise ConfigurationError(
                "UAR_TASK_API_MAX_RESPONSE_BYTES must be in 1024..1048576"
            )
        if (
            isinstance(self.task_api_timeout_seconds, bool)
            or not isinstance(self.task_api_timeout_seconds, (int, float))
            or not math.isfinite(self.task_api_timeout_seconds)
            or self.task_api_timeout_seconds <= 0
        ):
            raise ConfigurationError("UAR_TASK_API_TIMEOUT_SECONDS must be positive")

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "ApplicationSettings":
        values = os.environ if environment is None else environment
        driver = _required(values, "UAR_RUNTIME_DRIVER").lower()
        if driver != RuntimeDriver.DOCKER.value:
            raise ConfigurationError("UAR_RUNTIME_DRIVER must be docker")
        network_mode = _required(values, "UAR_DOCKER_NETWORK_MODE").lower()
        if network_mode not in {"none", "bridge"}:
            raise ConfigurationError("UAR_DOCKER_NETWORK_MODE must be none or bridge")
        network_host: str | None = None
        network_port: int | None = None
        if network_mode == "bridge":
            network_host = _required(values, "UAR_DOCKER_NETWORK_HOST")
            network_port = _port(values, "UAR_DOCKER_NETWORK_PORT")
        workspace_target = _required(values, "UAR_DOCKER_WORKSPACE_TARGET")
        if not workspace_target.startswith("/"):
            raise ConfigurationError("UAR_DOCKER_WORKSPACE_TARGET must be absolute")
        storage_root = Path(_required(values, "UAR_QWEN_SESSION_STORAGE_ROOT"))
        if storage_root.resolve() == Path(storage_root.resolve().anchor):
            raise ConfigurationError(
                "UAR_QWEN_SESSION_STORAGE_ROOT must not be a filesystem root"
            )
        task_token_secret_id = values.get("UAR_TASK_API_TOKEN_SECRET_ID", "").strip()
        task_token = values.get("UAR_TASK_API_TOKEN", "")
        if bool(task_token_secret_id) != bool(task_token):
            raise ConfigurationError(
                "Task API token ID and value must be configured together"
            )
        return cls(
            api_host=_required(values, "UAR_API_HOST"),
            api_port=_port(values, "UAR_API_PORT"),
            runtime_driver=RuntimeDriver(driver),
            docker_workload_key=_required(values, "UAR_DOCKER_WORKLOAD_KEY"),
            docker_image=_required(values, "UAR_DOCKER_WORKLOAD_IMAGE"),
            docker_command=_command(values),
            docker_user=_required(values, "UAR_DOCKER_WORKLOAD_USER"),
            docker_workspace_target=workspace_target,
            docker_network_mode=network_mode,
            docker_network_host=network_host,
            docker_network_port=network_port,
            docker_healthcheck_interval_seconds=_positive_float(
                values, "UAR_DOCKER_HEALTHCHECK_INTERVAL_SECONDS"
            ),
            docker_healthcheck_timeout_seconds=_positive_float(
                values, "UAR_DOCKER_HEALTHCHECK_TIMEOUT_SECONDS"
            ),
            docker_healthcheck_retries=_positive_int(
                values, "UAR_DOCKER_HEALTHCHECK_RETRIES"
            ),
            docker_healthcheck_start_period_seconds=_positive_float(
                values, "UAR_DOCKER_HEALTHCHECK_START_PERIOD_SECONDS"
            ),
            agent_cpu_cores=_positive_float(values, "UAR_AGENT_CPU_CORES"),
            agent_memory_bytes=_positive_int(values, "UAR_AGENT_MEMORY_BYTES"),
            agent_operation_timeout_seconds=_positive_float(
                values, "UAR_AGENT_OPERATION_TIMEOUT_SECONDS"
            ),
            agent_readiness_timeout_seconds=_positive_float(
                values, "UAR_AGENT_READINESS_TIMEOUT_SECONDS"
            ),
            agent_readiness_poll_interval_seconds=_positive_float(
                values, "UAR_AGENT_READINESS_POLL_INTERVAL_SECONDS"
            ),
            qwen_storage_root=storage_root,
            qwen_base_url=_required(values, "UAR_QWEN_BASE_URL"),
            qwen_model=_required(values, "UAR_QWEN_MODEL"),
            qwen_api_key_secret_id=_identifier(values, "UAR_QWEN_API_KEY_SECRET_ID"),
            qwen_api_key=_required(values, "UAR_QWEN_API_KEY"),
            task_api_base_url=_optional_task_endpoint(values),
            task_api_token_secret_id=task_token_secret_id or None,
            task_api_token=task_token or None,
            task_api_timeout_seconds=_positive_float(
                {"UAR_TASK_API_TIMEOUT_SECONDS": "10", **values},
                "UAR_TASK_API_TIMEOUT_SECONDS",
            ),
            task_api_max_response_bytes=_positive_int(
                {"UAR_TASK_API_MAX_RESPONSE_BYTES": "65536", **values},
                "UAR_TASK_API_MAX_RESPONSE_BYTES",
            ),
            stream_heartbeat_seconds=_positive_float(
                {"UAR_STREAM_HEARTBEAT_SECONDS": "15", **values},
                "UAR_STREAM_HEARTBEAT_SECONDS",
            ),
            stream_send_timeout_seconds=_positive_float(
                {"UAR_STREAM_SEND_TIMEOUT_SECONDS": "10", **values},
                "UAR_STREAM_SEND_TIMEOUT_SECONDS",
            ),
            chat_max_message_characters=_positive_int(
                {"UAR_CHAT_MAX_MESSAGE_CHARACTERS": "16384", **values},
                "UAR_CHAT_MAX_MESSAGE_CHARACTERS",
            ),
            chat_max_response_characters=_positive_int(
                {"UAR_CHAT_MAX_RESPONSE_CHARACTERS": "16384", **values},
                "UAR_CHAT_MAX_RESPONSE_CHARACTERS",
            ),
            chat_max_history_messages=_positive_int(
                {"UAR_CHAT_MAX_HISTORY_MESSAGES": "100", **values},
                "UAR_CHAT_MAX_HISTORY_MESSAGES",
            ),
            chat_max_history_page_size=_positive_int(
                {"UAR_CHAT_MAX_HISTORY_PAGE_SIZE": "50", **values},
                "UAR_CHAT_MAX_HISTORY_PAGE_SIZE",
            ),
        )
