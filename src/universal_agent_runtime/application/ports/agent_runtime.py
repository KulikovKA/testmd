"""Порт жизненного цикла runtime-единицы исполнения."""

from typing import Protocol

from universal_agent_runtime.application.ports.runtime_values import (
    CreateRuntimeRequest,
    DeleteResult,
    OperationOptions,
    RuntimeHandle,
    RuntimeObservation,
)


class AgentRuntime(Protocol):
    """Runtime-neutral lifecycle only; conversation uses a separate future port.

    Each call is bounded by options; native async cancellation must propagate.
    Expected failures are RuntimeFailure; invalid values raise ValueError/TypeError.
    The Orchestrator, not this port, owns Agent lifecycle policy and turn admission.
    """

    async def create(
        self, request: CreateRuntimeRequest, *, options: OperationOptions
    ) -> RuntimeObservation:
        """Provision inactive resources once per Agent; retry the identical request."""
        ...

    async def start(
        self, handle: RuntimeHandle, *, options: OperationOptions
    ) -> RuntimeObservation:
        """Ensure execution; success alone does not confirm agent readiness."""
        ...

    async def status(
        self, handle: RuntimeHandle, *, options: OperationOptions
    ) -> RuntimeObservation:
        """Observe without modifying resources; do not promote Agent lifecycle state."""
        ...

    async def stop(
        self, handle: RuntimeHandle, *, options: OperationOptions
    ) -> RuntimeObservation:
        """Ensure inactivity, retaining the workspace and its contents."""
        ...

    async def delete(
        self, handle: RuntimeHandle, *, options: OperationOptions
    ) -> DeleteResult:
        """Confirm removal including workspace; refuse a known executing instance."""
        ...
