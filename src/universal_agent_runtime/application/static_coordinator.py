"""Deterministic coordinator for local planning and unit tests."""

from hashlib import sha256

from universal_agent_runtime.domain.orchestration import (
    ExecutionGraph, UserTask, WorkerSpec, WorkerResources,
)


class StaticCoordinator:
    def __init__(self, resources: WorkerResources | None = None) -> None:
        self.resources = resources or WorkerResources(1, 1073741824)

    def plan(self, task: UserTask) -> ExecutionGraph:
        token = sha256(task.user_task_id.encode()).hexdigest()[:24]
        graph = ExecutionGraph(f"graph-{token}")
        for role, dependencies, skills in (
            ("architect", (), ("development-planning",)),
            ("developer", ("architect",), ("code-implementation",)),
            ("tester", ("developer",), ("code-testing",)),
            ("reviewer", ("developer",), ("code-review",)),
        ):
            graph.add_worker(WorkerSpec(
                worker_id=f"{token}-{role}",
                role=role,
                goal=f"{role}: {task.specification}",
                dependencies=tuple(f"{token}-{dep}" for dep in dependencies),
                skills=skills,
                resources=self.resources,
                workspace_reference=f"{token}-{role}-workspace",
            ))
        return graph
