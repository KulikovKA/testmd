"""Adapter test instrumentation, deliberately separate from the production port."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import ParamSpec, Protocol, cast

from universal_agent_runtime.application.ports.agent_runtime import AgentRuntime
from universal_agent_runtime.application.ports.runtime_errors import (
    RuntimeErrorCode,
    RuntimeOperation,
)
from universal_agent_runtime.application.ports.runtime_values import (
    CreateRuntimeRequest,
    ExecutionState,
    OperationOptions,
    RuntimeHandle,
)


class Phase(str, Enum):
    BEFORE = "before"
    ALLOCATED = "allocated"
    COMMITTED = "committed"


@dataclass
class Pause:
    """A deterministic operation barrier; no polling or arbitrary sleep required."""

    entered: asyncio.Event
    release: asyncio.Event


P = ParamSpec("P")


def scenario(function: Callable[P, Awaitable[None]]) -> Callable[P, None]:
    """Run async scenarios with stdlib only and a bounded test safety deadline."""

    @wraps(function)
    def run(*args: P.args, **kwargs: P.kwargs) -> None:
        async def bounded() -> None:
            harness = cast("RuntimeHarness | None", kwargs.get("runtime_harness"))
            budget = harness.options.timeout_seconds * 30 if harness is not None else 5
            try:
                async with asyncio.timeout(budget):
                    await function(*args, **kwargs)
            finally:
                # Keep adapter clients and their cleanup on the same event loop.
                if harness is not None:
                    async with asyncio.timeout(budget):
                        await harness.close()

        asyncio.run(bounded())

    return run


class RuntimeHarness(Protocol):
    """Future adapters supply this fixture and reuse tests/contract unchanged.

    Fault injection and resource inspection must use test-owned infrastructure.
    They are never methods on AgentRuntime or application-facing capabilities.
    """

    @property
    def runtime(self) -> AgentRuntime: ...

    @property
    def options(self) -> OperationOptions: ...

    @property
    def interruption_options(self) -> OperationOptions: ...

    @property
    def resources_per_runtime(self) -> int:
        """Expected inventory size for the harness workload, including its workspace."""
        ...

    def request(self, suffix: str = "one") -> CreateRuntimeRequest: ...

    def fail_next(
        self, operation: RuntimeOperation, phase: Phase, code: RuntimeErrorCode
    ) -> None: ...

    def pause_next(self, operation: RuntimeOperation, phase: Phase) -> Pause: ...

    def fail_cleanup_once(self) -> None: ...

    async def confirm_ready(self, handle: RuntimeHandle) -> None: ...

    async def force_state(
        self, handle: RuntimeHandle, state: ExecutionState
    ) -> None: ...

    async def write_workspace(self, handle: RuntimeHandle, value: str) -> None: ...

    async def read_workspace(self, handle: RuntimeHandle) -> str: ...

    async def resource_count(self) -> int: ...

    async def close(self) -> None: ...
