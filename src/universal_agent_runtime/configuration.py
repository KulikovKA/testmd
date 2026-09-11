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
    KATA = "kata"


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


def _optional_sfera_endpoint(environment: Mapping[str, str]) -> str | None:
    value = environment.get("UAR_SFERA_BASE_URL", "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path.rstrip("/")
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError("UAR_SFERA_BASE_URL must be an HTTP(S) origin")
    return value.rstrip("/")


def _optional_sfera_ca_cert_path(environment: Mapping[str, str]) -> Path | None:
    raw = environment.get("UAR_SFERA_CA_CERT_PATH", "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ConfigurationError("UAR_SFERA_CA_CERT_PATH must be an absolute path")
    try:
        certificate = candidate.resolve(strict=True)
        size = certificate.stat().st_size
        contents = certificate.read_bytes()
    except (OSError, ValueError):
        raise ConfigurationError(
            "UAR_SFERA_CA_CERT_PATH must reference a readable PEM file"
        ) from None
    if (
        not certificate.is_file()
        or not 1 <= size <= 1_048_576
        or b"-----BEGIN CERTIFICATE-----" not in contents
    ):
        raise ConfigurationError(
            "UAR_SFERA_CA_CERT_PATH must reference a readable PEM file"
        )
    return certificate


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
    qwen_reasoning_directive: str = "/think"
    sfera_base_url: str | None = None
    sfera_username_secret_id: str | None = None
    sfera_username: str | None = field(default=None, repr=False)
    sfera_password_secret_id: str | None = None
    sfera_password: str | None = field(default=None, repr=False)
    sfera_default_owner: str | None = None
    sfera_ca_cert_path: Path | None = None
    sfera_timeout_seconds: float = 10.0
    sfera_max_response_bytes: int = 65_536
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
        if self.sfera_base_url is not None:
            _optional_sfera_endpoint({"UAR_SFERA_BASE_URL": self.sfera_base_url})
        sfera_credentials = (
            self.sfera_username_secret_id,
            self.sfera_username,
            self.sfera_password_secret_id,
            self.sfera_password,
        )
        if any(value is not None for value in sfera_credentials) and any(
            value is None for value in sfera_credentials
        ):
            raise ConfigurationError(
                "Sfera username/password IDs and values must be configured together"
            )
        if self.sfera_base_url is None and any(
            value is not None for value in sfera_credentials
        ):
            raise ConfigurationError("Sfera credentials require UAR_SFERA_BASE_URL")
        if self.sfera_base_url is not None and any(
            value is None for value in sfera_credentials
        ):
            raise ConfigurationError("Sfera configuration requires username and password")
        if self.sfera_ca_cert_path is not None and self.sfera_base_url is None:
            raise ConfigurationError("UAR_SFERA_CA_CERT_PATH requires UAR_SFERA_BASE_URL")
        if self.sfera_default_owner is not None:
            _identifier(
                {"UAR_SFERA_DEFAULT_OWNER": self.sfera_default_owner},
                "UAR_SFERA_DEFAULT_OWNER",
            )
            if self.sfera_base_url is None:
                raise ConfigurationError(
                    "UAR_SFERA_DEFAULT_OWNER requires UAR_SFERA_BASE_URL"
                )
        if self.qwen_reasoning_directive not in {"/think", "/no_think"}:
            raise ConfigurationError(
                "UAR_QWEN_REASONING_DIRECTIVE must be /think or /no_think"
            )
        if self.sfera_username_secret_id is not None:
            _identifier(
                {"UAR_SFERA_USERNAME_SECRET_ID": self.sfera_username_secret_id},
                "UAR_SFERA_USERNAME_SECRET_ID",
            )
            _identifier(
                {"UAR_SFERA_PASSWORD_SECRET_ID": self.sfera_password_secret_id or ""},
                "UAR_SFERA_PASSWORD_SECRET_ID",
            )
            if (
                not self.sfera_username
                or not self.sfera_password
                or "\x00" in self.sfera_username
                or "\x00" in self.sfera_password
            ):
                raise ConfigurationError("Sfera credentials are invalid")
        if (
            type(self.sfera_max_response_bytes) is not int
            or not 1_024 <= self.sfera_max_response_bytes <= 1_048_576
        ):
            raise ConfigurationError(
                "UAR_SFERA_MAX_RESPONSE_BYTES must be in 1024..1048576"
            )
        if (
            isinstance(self.sfera_timeout_seconds, bool)
            or not isinstance(self.sfera_timeout_seconds, (int, float))
            or not math.isfinite(self.sfera_timeout_seconds)
            or self.sfera_timeout_seconds <= 0
        ):
            raise ConfigurationError("UAR_SFERA_TIMEOUT_SECONDS must be positive")

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "ApplicationSettings":
        values = os.environ if environment is None else environment
        driver_name = _required(values, "UAR_RUNTIME_DRIVER").lower()
        try:
            driver = RuntimeDriver(driver_name)
        except ValueError:
            raise ConfigurationError(
                "UAR_RUNTIME_DRIVER must be docker or kata"
            ) from None
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
        sfera_username_secret_id = values.get("UAR_SFERA_USERNAME_SECRET_ID", "").strip()
        sfera_username = values.get("UAR_SFERA_USERNAME", "")
        sfera_password_secret_id = values.get("UAR_SFERA_PASSWORD_SECRET_ID", "").strip()
        sfera_password = values.get("UAR_SFERA_PASSWORD", "")
        sfera_default_owner = values.get("UAR_SFERA_DEFAULT_OWNER", "").strip()
        return cls(
            api_host=_required(values, "UAR_API_HOST"),
            api_port=_port(values, "UAR_API_PORT"),
            runtime_driver=driver,
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
            qwen_reasoning_directive=values.get(
                "UAR_QWEN_REASONING_DIRECTIVE", "/think"
            ),
            sfera_base_url=_optional_sfera_endpoint(values),
            sfera_username_secret_id=sfera_username_secret_id or None,
            sfera_username=sfera_username or None,
            sfera_password_secret_id=sfera_password_secret_id or None,
            sfera_password=sfera_password or None,
            sfera_default_owner=sfera_default_owner or None,
            sfera_ca_cert_path=_optional_sfera_ca_cert_path(values),
            sfera_timeout_seconds=_positive_float(
                {"UAR_SFERA_TIMEOUT_SECONDS": "10", **values},
                "UAR_SFERA_TIMEOUT_SECONDS",
            ),
            sfera_max_response_bytes=_positive_int(
                {"UAR_SFERA_MAX_RESPONSE_BYTES": "65536", **values},
                "UAR_SFERA_MAX_RESPONSE_BYTES",
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
