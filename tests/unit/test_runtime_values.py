"""Trust-boundary validation, immutability and diagnostic safety."""

from dataclasses import FrozenInstanceError, replace
from typing import Any
from uuid import UUID

import pytest

from universal_agent_runtime.application.ports.runtime_errors import (
    RuntimeErrorCode,
    RuntimeFailure,
    RuntimeOperation,
)
from universal_agent_runtime.application.ports.runtime_values import (
    CreateRuntimeRequest,
    DeleteResult,
    EnvironmentVariable,
    ExecutionState,
    NetworkDestination,
    OperationOptions,
    Readiness,
    ResourceLimits,
    RuntimeHandle,
    RuntimeObservation,
    SecretBinding,
)
from universal_agent_runtime.domain.identifiers import AgentId, WorkspaceId


def request() -> CreateRuntimeRequest:
    return CreateRuntimeRequest(
        AgentId("one"),
        WorkspaceId("ws-one"),
        "test-workload",
        ResourceLimits(0.5, 1024),
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "../one",
        "a/b",
        "a\\b",
        ".",
        "..",
        "a.b",
        "a b",
        "one\n",
        "a" * 65,
        "а",
        None,
        3,
    ],
)
@pytest.mark.parametrize("identity", [AgentId, WorkspaceId])
def test_invalid_identifiers_reject_paths_and_ambiguous_text(
    identity: Any, value: Any
) -> None:
    with pytest.raises(ValueError):
        identity(value)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, "1", None])
def test_invalid_budgets_and_cpu_limits(value: Any) -> None:
    with pytest.raises(ValueError):
        OperationOptions(value)
    with pytest.raises(ValueError):
        ResourceLimits(value, 1024)


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "1024", None])
def test_memory_must_be_positive_integer(value: Any) -> None:
    with pytest.raises(ValueError):
        ResourceLimits(1.0, value)


@pytest.mark.parametrize(
    "host",
    [
        "",
        "https://example.invalid",
        "*.example.invalid",
        "one/../../two",
        "one:80",
        "one\n",
        "a..b",
        "-a",
        "a-",
        "a" * 64,
        "fe80::1%eth0",
    ],
)
def test_network_destination_is_not_an_arbitrary_url(host: str) -> None:
    with pytest.raises(ValueError):
        NetworkDestination(host, 443)


@pytest.mark.parametrize("host", ["service.example.invalid", "127.0.0.1", "::1"])
def test_explicit_dns_and_ip_requirements_are_valid(host: str) -> None:
    assert NetworkDestination(host, 443).host == host


@pytest.mark.parametrize("port", [0, 65536, -1, 1.5, True, "80"])
def test_network_port_validation(port: Any) -> None:
    with pytest.raises(ValueError):
        NetworkDestination("example.invalid", port)


@pytest.mark.parametrize("name", ["", "A=B", "A B", "A\n", "1A"])
def test_environment_and_secret_binding_names(name: str) -> None:
    with pytest.raises(ValueError):
        EnvironmentVariable(name, "value")
    with pytest.raises(ValueError):
        SecretBinding(name, "reference")


def test_configuration_is_immutable_and_default_deny() -> None:
    spec = request()
    assert spec.network == ()
    assert spec.environment == ()
    assert spec.secrets == ()
    with pytest.raises(FrozenInstanceError):
        spec.workload = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError):
        replace(spec, environment=[EnvironmentVariable("NAME", "value")])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        replace(
            spec,
            environment=(
                EnvironmentVariable("NAME", "one"),
                EnvironmentVariable("NAME", "two"),
            ),
        )
    with pytest.raises(ValueError):
        replace(
            spec,
            environment=(EnvironmentVariable("NAME", "value"),),
            secrets=(SecretBinding("NAME", "secret-ref"),),
        )
    destination = NetworkDestination("example.invalid", 443)
    with pytest.raises(ValueError):
        replace(spec, network=(destination, destination))
    with pytest.raises(ValueError):
        EnvironmentVariable("NAME", "invalid\x00value")
    with pytest.raises(ValueError):
        SecretBinding("NAME", "../../private")


def test_execution_does_not_imply_readiness() -> None:
    handle = RuntimeHandle(AgentId("one"), UUID(int=1))
    assert (
        RuntimeObservation(
            handle, WorkspaceId("ws"), ExecutionState.EXECUTING
        ).readiness
        is Readiness.UNCONFIRMED
    )
    for state in (
        ExecutionState.INACTIVE,
        ExecutionState.FAULTED,
        ExecutionState.UNKNOWN,
    ):
        with pytest.raises(ValueError):
            RuntimeObservation(handle, WorkspaceId("ws"), state, Readiness.CONFIRMED)


@pytest.mark.parametrize(
    "changes",
    [
        {"agent_id": "one"},
        {"workspace_id": "ws-one"},
        {"workload": "../workload"},
        {"resources": {}},
        {"environment": ("raw",)},
        {"secrets": ("raw",)},
        {"network": ("raw",)},
    ],
)
def test_create_rejects_untyped_or_unsafe_inputs(changes: dict[str, Any]) -> None:
    with pytest.raises((ValueError, TypeError)):
        replace(request(), **changes)


def test_observation_and_handles_reject_foreign_values() -> None:
    handle = RuntimeHandle(AgentId("one"), UUID(int=1))
    invalid: Any = "backend-object"
    for build in (
        lambda: RuntimeHandle(invalid, UUID(int=1)),
        lambda: RuntimeHandle(AgentId("one"), invalid),
        lambda: RuntimeObservation(invalid, WorkspaceId("ws"), ExecutionState.INACTIVE),
        lambda: RuntimeObservation(handle, invalid, ExecutionState.INACTIVE),
        lambda: RuntimeObservation(handle, WorkspaceId("ws"), invalid),
        lambda: RuntimeObservation(
            handle, WorkspaceId("ws"), ExecutionState.EXECUTING, invalid
        ),
        lambda: DeleteResult(invalid),
        lambda: NetworkDestination("example.invalid", 443, invalid),
    ):
        with pytest.raises(TypeError):
            build()


def test_diagnostics_do_not_reflect_environment_or_opaque_references() -> None:
    spec = replace(
        request(),
        environment=(EnvironmentVariable("NAME", "synthetic-sensitive-value"),),
        secrets=(SecretBinding("KEY", "synthetic-secret-ref"),),
        network=(NetworkDestination("private.example.invalid", 443),),
    )
    handle = RuntimeHandle(spec.agent_id, UUID(int=12345))
    failure = RuntimeFailure(
        RuntimeOperation.CREATE, spec.agent_id, RuntimeErrorCode.CLEANUP_FAILED, handle
    )
    rendered = repr(spec) + repr(handle) + str(failure) + repr(failure)
    for protected in (
        "synthetic-sensitive-value",
        "synthetic-secret-ref",
        "private.example.invalid",
        str(handle.reference),
    ):
        assert protected not in rendered
    assert failure.handle == handle
    assert failure.operation is RuntimeOperation.CREATE
    assert failure.agent_id == spec.agent_id
    with pytest.raises(ValueError):
        RuntimeFailure(
            RuntimeOperation.DELETE,
            AgentId("another"),
            RuntimeErrorCode.NOT_FOUND,
            handle,
        )


@pytest.mark.parametrize("code", list(RuntimeErrorCode))
def test_retry_classification_requires_same_identity_reconciliation(
    code: RuntimeErrorCode,
) -> None:
    failure = RuntimeFailure(RuntimeOperation.CREATE, AgentId("one"), code)
    assert failure.retryable == (
        code
        in {
            RuntimeErrorCode.TIMEOUT,
            RuntimeErrorCode.UNAVAILABLE,
            RuntimeErrorCode.CLEANUP_FAILED,
        }
    )
