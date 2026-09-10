"""TASK-013 package contract and safe task-decomposition behavior fixture tests."""

import json
from pathlib import Path

import pytest

from universal_agent_runtime.adapters.skill_packages import (
    SkillPackageCatalog,
    SkillPackageError,
    load_skill_package,
)


def test_builtin_task_decomposition_is_versioned_and_instructs_safe_flow() -> None:
    catalog = SkillPackageCatalog.builtins()
    ((skill, effective),) = catalog.resolve(
        ("task-decomposition",),
        ("get_task", "create_task", "create_subtask", "update_task"),
    )

    assert skill.version == "1.0.0"
    assert effective == skill.tool_capabilities
    for expected in (
        "Propose a structured decomposition before creating",
        "Ask for explicit confirmation",
        "Never call a Task mutation tool before explicit confirmation",
        "If the user cancels, stop without writing",
        "partially fails",
        "duplicate is possible",
    ):
        assert expected in skill.instructions
    fragment = skill.prompt_fragment(("get_task", "create_task"))
    assert "Effective tool capabilities: get_task, create_task" in fragment
    assert "The selection does not add tool capabilities" in fragment


def test_skill_effective_tools_are_an_intersection_not_an_authorization_grant() -> None:
    catalog = SkillPackageCatalog.builtins()
    ((_, effective),) = catalog.resolve(
        ("task-decomposition",), ("get_task", "update_task", "unrelated")
    )

    assert effective == ("get_task", "update_task")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update({"version": "one"}),
        lambda data: data.update({"instruction_file": "../escape.md"}),
        lambda data: data.update({"unknown": True}),
        lambda data: data.update({"tool_capabilities": ["get_task", "get_task"]}),
    ],
)
def test_malformed_manifest_is_rejected(tmp_path: Path, mutation: object) -> None:
    source = (
        SkillPackageCatalog.builtins()
        .resolve(("task-decomposition",), ())[0][0]
        .source_directory
    )
    package = tmp_path / "package"
    package.mkdir()
    (package / "SKILL.md").write_text(
        (source / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    manifest = json.loads((source / "skill.json").read_text(encoding="utf-8"))
    assert callable(mutation)
    mutation(manifest)
    (package / "skill.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SkillPackageError):
        load_skill_package(package)


def test_unknown_skill_cannot_be_silently_delivered() -> None:
    with pytest.raises(SkillPackageError):
        SkillPackageCatalog.builtins().resolve(("not-installed",), ())
