import unittest

from universal_agent_runtime.domain.development_task import (
    TERMINAL_STATES,
    DevelopmentFailure,
    DevelopmentRequest,
    DevelopmentResult,
    DevelopmentTask,
    RepositoryTarget,
    validate_branch,
)
from universal_agent_runtime.domain.development_task import (
    DevelopmentState as S,
)
from universal_agent_runtime.domain.identifiers import AgentId


class DevelopmentDomainTests(unittest.TestCase):
    def task(self, **options):
        return DevelopmentTask(
            "task-one",
            DevelopmentRequest(
                AgentId("agent-one"), "Create a Java calculator", **options
            ),
        )

    def test_success_clarification_and_bounded_fix_loop(self):
        task = self.task(
            repository=RepositoryTarget("group/team", "project"), publish=True
        )
        for state in (
            S.ANALYZING_REQUIREMENTS,
            S.WAITING_FOR_CLARIFICATION,
            S.ANALYZING_REQUIREMENTS,
            S.PLANNING,
            S.PREPARING_WORKSPACE,
            S.CREATING_REPOSITORY,
            S.IMPLEMENTING,
            S.TESTING,
            S.FIXING,
            S.TESTING,
            S.REVIEWING,
            S.COMMITTING,
            S.PUSHING,
        ):
            task = task.transition(state)
        result = DevelopmentResult(
            "main", "a" * 40, ("pom.xml",), ("mvn test",), "repo-one", True
        )
        task = task.transition(S.COMPLETED, result=result)
        self.assertEqual(task.result, result)
        self.assertEqual(task.fix_attempts, 1)
        self.assertEqual(task.version, len(task.transitions) - 1)
        with self.assertRaises(DevelopmentFailure):
            task.transition(S.FAILED)

    def test_cannot_skip_checks_or_reopen_terminal_states(self):
        for state in (S.COMPLETED, S.PUSHING, S.REVIEWING, S.TESTING):
            with self.subTest(state=state), self.assertRaises(DevelopmentFailure):
                self.task().transition(state)
        for state in TERMINAL_STATES - {S.COMPLETED}:
            terminal = self.task().transition(state)
            for target in S:
                with (
                    self.subTest(state=state, target=target),
                    self.assertRaises(DevelopmentFailure),
                ):
                    terminal.transition(target)

    def test_fix_budget_is_enforced_by_domain(self):
        task = self.task(max_fix_attempts=0)
        for state in (
            S.ANALYZING_REQUIREMENTS,
            S.PLANNING,
            S.PREPARING_WORKSPACE,
            S.IMPLEMENTING,
            S.TESTING,
        ):
            task = task.transition(state)
        with self.assertRaises(DevelopmentFailure) as raised:
            task.transition(S.FIXING)
        self.assertEqual(raised.exception.code, "fix_limit")

    def test_branch_and_publication_cannot_inject_git_options(self):
        for branch in (
            "--all",
            "../main",
            "refs/../x",
            "x.lock",
            "x/.hidden",
            "x//y",
            "main\n--force",
            "HEAD",
        ):
            with self.subTest(branch=branch), self.assertRaises(ValueError):
                validate_branch(branch)
        self.assertEqual(validate_branch("feature/java-agent"), "feature/java-agent")
        with self.assertRaises(ValueError):
            self.task(publish=True)
        with self.assertRaises(ValueError):
            self.task(max_fix_attempts=True)
