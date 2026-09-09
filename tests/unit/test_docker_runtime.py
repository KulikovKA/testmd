from unittest.mock import Mock

import pytest

from universal_agent_runtime.adapters.docker_runtime import (
    DockerRuntime,
    DockerWorkload,
)


def test_workload_requires_explicit_safe_settings() -> None:
    with pytest.raises(ValueError):
        DockerWorkload("", ("run",), "65534")
    with pytest.raises(ValueError):
        DockerWorkload("image", (), "65534")
    with pytest.raises(ValueError):
        DockerWorkload("image", ("run",), "")
    with pytest.raises(ValueError):
        DockerWorkload("image", ("run",), "65534", "relative")


def test_driver_requires_a_workload_catalog() -> None:
    with pytest.raises(ValueError):
        DockerRuntime({}, client=Mock())


def test_docker_types_are_confined_to_adapter_module() -> None:
    from universal_agent_runtime.application.ports.agent_runtime import AgentRuntime
    from universal_agent_runtime.application.ports.runtime_values import RuntimeHandle

    assert "docker" not in AgentRuntime.__module__
    assert "docker" not in RuntimeHandle.__module__
