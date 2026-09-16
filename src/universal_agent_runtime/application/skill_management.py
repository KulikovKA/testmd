"""Skill installation and discovery use cases."""

from universal_agent_runtime.application.ports.skill_store import (
    SkillDescriptor,
    SkillInstallRequest,
    SkillStore,
)


class SkillManagementService:
    def __init__(self, store: SkillStore) -> None:
        self._store = store

    def list_skills(self) -> tuple[SkillDescriptor, ...]:
        return self._store.list_skills()

    def get_skill(self, identifier: str) -> SkillDescriptor | None:
        return self._store.get_skill(identifier)

    def install(self, request: SkillInstallRequest) -> SkillDescriptor:
        return self._store.install(request)
