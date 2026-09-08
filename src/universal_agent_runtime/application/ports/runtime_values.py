"""Immutable runtime requirements and observations, independent of any backend."""

import ipaddress
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID

from universal_agent_runtime.domain.identifiers import (
    AgentId,
    WorkspaceId,
    validate_identifier,
)


class ExecutionState(str, Enum):
    INACTIVE = "inactive"
    EXECUTING = "executing"
    FAULTED = "faulted"
    UNKNOWN = "unknown"


class Readiness(str, Enum):
    UNCONFIRMED = "unconfirmed"
    CONFIRMED = "confirmed"


class NetworkProtocol(str, Enum):
    TCP = "tcp"
    UDP = "udp"


def _positive_number(value: float, label: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{label} must be finite and positive")


@dataclass(frozen=True)
class RuntimeHandle:
    """Opaque reference scoped to an Agent and the configured adapter namespace."""

    agent_id: AgentId
    reference: UUID = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.agent_id, AgentId) or not isinstance(
            self.reference, UUID
        ):
            raise TypeError("invalid runtime handle")


@dataclass(frozen=True)
class ResourceLimits:
    cpu_cores: float
    memory_bytes: int

    def __post_init__(self) -> None:
        _positive_number(self.cpu_cores, "cpu_cores")
        if type(self.memory_bytes) is not int or self.memory_bytes <= 0:
            raise ValueError("memory_bytes must be a positive integer")


@dataclass(frozen=True)
class EnvironmentVariable:
    """Non-secret value only; credentials must use SecretBinding."""

    name: str
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*", self.name
        ):
            raise ValueError("invalid environment variable name")
        if not isinstance(self.value, str) or "\x00" in self.value:
            raise ValueError("invalid environment variable value")


@dataclass(frozen=True)
class SecretBinding:
    """Reference resolved by the adapter at execution time, never a secret value."""

    name: str
    secret_id: str = field(repr=False)

    def __post_init__(self) -> None:
        EnvironmentVariable(self.name, "")
        validate_identifier(self.secret_id)


@dataclass(frozen=True)
class NetworkDestination:
    """One explicit DNS name or IP and transport port; no arbitrary URLs/wildcards."""

    host: str = field(repr=False)
    port: int
    protocol: NetworkProtocol = NetworkProtocol.TCP

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host or len(self.host) > 253:
            raise ValueError("invalid network host")
        try:
            ipaddress.ip_address(self.host)
        except ValueError:
            if not all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                for label in self.host.split(".")
            ):
                raise ValueError("host must be an explicit DNS name or IP") from None
        if "%" in self.host:
            raise ValueError("scoped IP addresses are not portable")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("port must be an integer in 1..65535")
        if not isinstance(self.protocol, NetworkProtocol):
            raise TypeError("invalid network protocol")


@dataclass(frozen=True)
class CreateRuntimeRequest:
    """One Agent/workspace, with a logical deployment-owned workload reference."""

    agent_id: AgentId
    workspace_id: WorkspaceId
    workload: str
    resources: ResourceLimits
    environment: tuple[EnvironmentVariable, ...] = ()
    secrets: tuple[SecretBinding, ...] = ()
    network: tuple[NetworkDestination, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.agent_id, AgentId) or not isinstance(
            self.workspace_id, WorkspaceId
        ):
            raise TypeError("invalid Agent or Workspace identity")
        validate_identifier(self.workload)
        if not isinstance(self.resources, ResourceLimits):
            raise TypeError("invalid resource limits")
        for entries, entry_type in (
            (self.environment, EnvironmentVariable),
            (self.secrets, SecretBinding),
            (self.network, NetworkDestination),
        ):
            if not isinstance(entries, tuple) or not all(
                isinstance(entry, entry_type) for entry in entries
            ):
                raise ValueError(
                    "runtime requirements must contain immutable typed tuples"
                )
        names = [entry.name for entry in self.environment] + [
            entry.name for entry in self.secrets
        ]
        if len(names) != len(set(names)):
            raise ValueError("duplicate environment or secret binding name")
        if len(self.network) != len(set(self.network)):
            raise ValueError("duplicate network destination")


@dataclass(frozen=True)
class OperationOptions:
    """Bound one call, including admission/lock waits; cancellation is native async."""

    timeout_seconds: float

    def __post_init__(self) -> None:
        _positive_number(self.timeout_seconds, "timeout_seconds")


@dataclass(frozen=True)
class RuntimeObservation:
    handle: RuntimeHandle
    workspace_id: WorkspaceId
    execution: ExecutionState
    readiness: Readiness = Readiness.UNCONFIRMED

    def __post_init__(self) -> None:
        if not isinstance(self.handle, RuntimeHandle) or not isinstance(
            self.workspace_id, WorkspaceId
        ):
            raise TypeError("invalid observation identity")
        if not isinstance(self.execution, ExecutionState) or not isinstance(
            self.readiness, Readiness
        ):
            raise TypeError("invalid runtime observation")
        if (
            self.readiness is Readiness.CONFIRMED
            and self.execution is not ExecutionState.EXECUTING
        ):
            raise ValueError("only an executing runtime can have confirmed readiness")


@dataclass(frozen=True)
class DeleteResult:
    """Returned only after absence of all runtime-owned resources is confirmed."""

    handle: RuntimeHandle

    def __post_init__(self) -> None:
        if not isinstance(self.handle, RuntimeHandle):
            raise TypeError("invalid deleted runtime handle")
