"""Optional read-only runtime view of one Agent workspace."""

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from universal_agent_runtime.application.ports.runtime_values import (
    OperationOptions,
    RuntimeHandle,
)


@dataclass(frozen=True)
class WorkspaceEntry:
    path: str
    type: Literal["file", "directory"]
    category: Literal["skill", "qwen_transcript", "qwen_state", "uar_tool", "workspace"]
    size_bytes: int | None = None


@dataclass(frozen=True)
class WorkspaceInventory:
    available: bool
    files: tuple[WorkspaceEntry, ...] = ()
    truncated: bool = False


@runtime_checkable
class WorkspaceInventoryReader(Protocol):
    async def workspace_inventory(
        self, handle: RuntimeHandle, *, options: OperationOptions
    ) -> WorkspaceInventory: ...
