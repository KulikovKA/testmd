"""Prevent infrastructure or conversation mechanics from leaking into control."""

import ast
from pathlib import Path

from universal_agent_runtime.application.ports.agent_interaction import (
    AgentInteraction,
)
from universal_agent_runtime.application.ports.agent_runtime import AgentRuntime


def test_domain_and_application_import_only_inward_or_stdlib() -> None:
    package = Path(__file__).resolve().parents[2] / "src" / "universal_agent_runtime"
    allowed = {"re", "dataclasses", "enum", "uuid", "math", "ipaddress", "typing"}
    for boundary in ("domain", "application"):
        for path in (package / boundary).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    assert node.level == 0, f"Review relative boundary import in {path}"
                    modules = [node.module or ""]
                else:
                    continue
                for module in modules:
                    if module.startswith("universal_agent_runtime."):
                        target = module.split(".")[1]
                        assert target in (
                            {"domain"}
                            if boundary == "domain"
                            else {"domain", "application"}
                        )
                    else:
                        assert module.split(".")[0] in allowed, (
                            f"Review new dependency {module} in {path}"
                        )


def test_lifecycle_port_does_not_grow_a_conversation_transport() -> None:
    public = {name for name in AgentRuntime.__dict__ if not name.startswith("_")}
    assert public == {"create", "start", "status", "stop", "delete"}


def test_conversation_port_stays_separate_from_lifecycle_control() -> None:
    public = {
        name for name in AgentInteraction.__dict__ if not name.startswith("_")
    }
    assert public == {"create_session", "turn", "delete_session"}
