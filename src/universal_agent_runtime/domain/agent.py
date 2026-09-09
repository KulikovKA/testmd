"""Runtime-neutral Agent lifecycle vocabulary owned by the Orchestrator."""

from enum import Enum


class AgentLifecycleState(str, Enum):
    """Public lifecycle states; backend execution observations stay separate."""

    CREATING = "CREATING"
    STARTING = "STARTING"
    READY = "READY"
    BUSY = "BUSY"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
