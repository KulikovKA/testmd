"""Development stages reuse owned chat turns and the existing bounded AG-UI channel."""

import asyncio
import json
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import uuid4

from universal_agent_runtime.application.agent_chat import (
    AcceptedTurn,
    AgentChatService,
    TurnDeltas,
)
from universal_agent_runtime.application.agent_lifecycle import (
    AgentLifecycleErrorCode,
    AgentLifecycleFailure,
    AgentLifecycleOperation,
)
from universal_agent_runtime.application.development_tasks import DevelopmentTaskService
from universal_agent_runtime.application.observability_text import observable_text
from universal_agent_runtime.application.ports.development_workspace import (
    DevelopmentWorkspacePort,
    ProjectFile,
    WorkspaceRequest,
)
from universal_agent_runtime.application.ports.development_workspace import (
    WorkspaceOperation as W,
)
from universal_agent_runtime.application.ports.repository_credentials import (
    SecretPolicyPort,
)
from universal_agent_runtime.application.ports.trusted_git import (
    GitRequest,
    TrustedGitPort,
)
from universal_agent_runtime.domain.development_task import (
    TERMINAL_STATES,
    DevelopmentFailure,
    DevelopmentRequest,
    DevelopmentResult,
)
from universal_agent_runtime.domain.development_task import DevelopmentState as S
from universal_agent_runtime.domain.message import Message

JAVA_SKILLS = (
    "requirements-clarification",
    "development-planning",
    "java-project-setup",
    "java-implementation",
    "code-review",
)


class DevelopmentWorkflow:
    def __init__(
        self,
        service: DevelopmentTaskService,
        chat: AgentChatService,
        workspace: DevelopmentWorkspacePort,
        secrets: SecretPolicyPort,
        git: TrustedGitPort | None = None,
    ) -> None:
        self.service, self.chat, self.workspace = service, chat, workspace
        self.git, self.secrets = git, secrets
        self._pending: set[asyncio.Task] = set()

    def create(self, request: DevelopmentRequest):
        self.secrets.reject(request.specification)
        self.secrets.reject(request.branch)
        if request.repository is not None:
            raise DevelopmentFailure("invalid_request")
        if request.repository_url is not None:
            if self.git is None:
                raise DevelopmentFailure("repository_unavailable")
            self.git.validate(request.repository_url)
        return self.service.create(request)

    def clarify(self, task_id: str, answer: str):
        self.secrets.reject(answer)
        return self.service.clarify(task_id, answer)

    def begin(self, task_id: str) -> AcceptedTurn:
        task = self.service.get(task_id)
        agent = self.service.agents.get(task.request.agent_id)
        if (
            agent is None
            or not set(JAVA_SKILLS).issubset(agent.configuration.skills)
            or agent.configuration.tools
        ):
            raise DevelopmentFailure("agent_unavailable")
        self.service.claim(task_id)
        run = _DevelopmentRun(self, task_id)
        owned = asyncio.create_task(run.execute())
        self._pending.add(owned)
        owned.add_done_callback(self._finished)
        return AcceptedTurn(
            task.request.agent_id,
            run.turn_id,
            owned,
            run.message_id,
            progress=run.progress,
        )

    def _finished(self, task: asyncio.Task) -> None:
        self._pending.discard(task)
        if not task.cancelled():
            task.exception()

    async def close(self) -> None:
        # Release only after native turns/operations have finished, including disconnect.
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)


class _DevelopmentRun:
    def __init__(self, workflow: DevelopmentWorkflow, task_id: str) -> None:
        self.w = workflow
        self.task_id = task_id
        self.request = workflow.service.get(task_id).request
        self.branch = (
            f"uar/{task_id}" if self.request.repository_url else self.request.branch
        )
        self.turn_id, self.message_id = uuid4().hex, uuid4().hex
        self.progress = TurnDeltas()
        self.text = ""
        self.phase = "task"
        self.attempt = 0
        self.step_name = None

    async def record(
        self, kind: str, status: str, summary: str, data=None, *, closing=False
    ):
        def safe(value):
            if isinstance(value, str):
                return observable_text(self.w.secrets.redact(value))
            if isinstance(value, list):
                return [safe(item) for item in value]
            if isinstance(value, dict):
                return {key: safe(item) for key, item in value.items()}
            return value

        event = self.w.service.record(
            self.task_id,
            type=kind,
            phase=self.phase,
            status=status,
            step_name=self.step_name,
            attempt=self.attempt,
            summary=safe(summary),
            data_json=json.dumps(safe(data or {}), ensure_ascii=False),
            closing=closing,
        )
        await self.progress.emit(event)

    async def finish_step(self, status="completed"):
        if self.step_name is not None:
            await self.record(
                "phase",
                status,
                "Этап завершён" if status == "completed" else "Этап остановлен",
                closing=True,
            )
            self.step_name = None

    def checkpoint(self) -> None:
        if self.w.service.get(self.task_id).cancel_requested:
            raise DevelopmentFailure("cancelled")

    async def stage(self, state: S, **changes) -> None:
        self.checkpoint()
        await self.finish_step()
        self.w.service.transition(self.task_id, state, **changes)
        phases = {
            S.ANALYZING_REQUIREMENTS: "requirements",
            S.PLANNING: "planning",
            S.PREPARING_WORKSPACE: "workspace",
            S.CLONING_REPOSITORY: "repository",
            S.IMPLEMENTING: "implementation",
            S.TESTING: "testing",
            S.REVIEWING: "review",
            S.FIXING: "fixing",
            S.COMMITTING: "committing",
            S.PUSHING: "pushing",
        }
        if state in phases:
            self.phase = phases[state]
            self.attempt = 1 + sum(
                e.type == "phase" and e.status == "started" and e.phase == self.phase
                for e in self.w.service.get(self.task_id).trace
            )
            self.step_name = f"{self.phase}:{self.attempt}"
            try:
                await self.record(
                    "phase", "started", "Начат этап", {"state": state.value}
                )
            except Exception:
                self.step_name = None
                raise
        else:
            self.phase, self.attempt, self.step_name = "task", 0, None
            await self.record(
                "task_status",
                "waiting" if state is S.WAITING_FOR_CLARIFICATION else "completed",
                "Нужно уточнение требований"
                if state is S.WAITING_FOR_CLARIFICATION
                else "Задача завершена",
                {"state": state.value},
                closing=True,
            )

    async def operation(self, operation: W, **kwargs):
        self.checkpoint()
        command = None
        if operation in {W.TEST, W.PACKAGE}:
            command = (
                "mvn " + ("test" if operation is W.TEST else "package")
                if self.request.build_system.value == "maven"
                else "gradle " + ("test" if operation is W.TEST else "build")
            )
            await self.record(
                "command_started",
                "started",
                "Запуск команды",
                {"operation": operation.value, "command": command},
            )
        try:
            result = await self.w.workspace.execute(
                self.request.agent_id,
                WorkspaceRequest(
                    self.task_id,
                    operation,
                    branch=self.branch,
                    build_system=self.request.build_system,
                    **kwargs,
                ),
            )
        except Exception:
            if command:
                await self.record(
                    "command_finished",
                    "failed",
                    "Команда не завершилась успешно",
                    {
                        "operation": operation.value,
                        "command": command,
                        "success": False,
                        "exit_code": None,
                    },
                    closing=True,
                )
            raise
        if command:
            # Only a validated command label can replace the planned build command.
            actual = (
                result.check
                if result.check
                in {
                    "mvn test",
                    "mvn package",
                    "gradle test",
                    "gradle build",
                    "./gradlew test",
                    "./gradlew build",
                }
                else command
            )
            await self.record(
                "command_finished",
                "completed" if result.success else "failed",
                "Команда завершена",
                {
                    "operation": operation.value,
                    "command": actual,
                    "success": result.success,
                    "exit_code": result.exit_code,
                    "execution_backend": self.w.workspace.execution_backend,
                },
            )
        if not result.success and operation not in {W.TEST, W.PACKAGE}:
            raise DevelopmentFailure("operation_failed")
        return result

    async def reason(self, payload: dict) -> dict:
        task = self.w.service.get(self.task_id)
        prompt = f"DEVELOPMENT_PHASE: {task.state.value}\n" + json.dumps(
            {
                "specification": self.request.specification,
                "clarification": task.clarification,
                "build_system": self.request.build_system.value,
                "java_version": 21,
                "plan": task.plan,
                "instruction": "Use the selected Java Skills. Return only the phase JSON object. No Markdown fences. File contents must be complete. Never claim unexecuted checks.",
                **payload,
            },
            ensure_ascii=False,
        )
        self.w.secrets.reject(prompt)
        if len(prompt) > 16384:
            raise DevelopmentFailure("output_limit")
        turn = self.w.chat.begin(
            self.request.agent_id, prompt, stream=True, development_task_id=self.task_id
        )
        agent = self.w.service.agents.get(self.request.agent_id)
        number = len(agent.messages) // 2 + 1
        started = time.perf_counter()
        # Keep the normal parser/redactor/coalescer and stream/final consistency
        # checks active, but detach internal JSON from the public text channel.
        if turn.deltas is not None:
            turn.deltas.detach()
        try:
            await self.record(
                "llm_turn_started",
                "started",
                "Вызов Qwen",
                {"turn": number, "phase": task.state.value},
            )
            messages = await asyncio.shield(turn.task)
            content = messages[-1].content
        except Exception:
            await self.record(
                "llm_turn_finished",
                "failed",
                "Вызов Qwen завершился ошибкой",
                {
                    "turn": number,
                    "phase": task.state.value,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                },
                closing=True,
            )
            raise
        finally:
            if turn.deltas is not None:
                turn.deltas.detach()
            # Do not release Agent while a native Qwen turn is still active.
            await asyncio.gather(asyncio.shield(turn.task), return_exceptions=True)
        await self.record(
            "llm_turn_finished",
            "completed",
            "Ответ Qwen получен",
            {
                "turn": number,
                "phase": task.state.value,
                "duration_ms": round((time.perf_counter() - started) * 1000),
            },
        )
        self.checkpoint()
        try:
            result = json.loads(content)
            if not isinstance(result, dict):
                raise TypeError
            return result
        except (ValueError, TypeError):
            raise DevelopmentFailure("invalid_model_result") from None

    def strings(self, value, maximum: int, *, empty: bool = False) -> tuple[str, ...]:
        if (
            not isinstance(value, list)
            or len(value) > maximum
            or (not empty and not value)
        ):
            raise DevelopmentFailure("invalid_model_result")
        if any(
            not isinstance(item, str)
            or not item.strip()
            or len(item) > 1000
            or "\x00" in item
            for item in value
        ):
            raise DevelopmentFailure("invalid_model_result")
        for item in value:
            self.w.secrets.reject(item)
        return tuple(value)

    async def files(self):
        result = await self.operation(W.INVENTORY)
        for file in result.files:
            self.w.secrets.reject(file.path)
            self.w.secrets.reject(file.content)
        return result.files

    async def implement(self, *, feedback: str = "") -> None:
        before = {file.path: file.content for file in await self.files()}
        value = await self.reason(
            {
                "files": [
                    {"path": path, "content": content}
                    for path, content in before.items()
                ],
                "feedback": feedback,
            }
        )
        try:
            if (
                set(value) != {"files"}
                or not isinstance(value["files"], list)
                or not 1 <= len(value["files"]) <= 64
            ):
                raise ValueError
            files = tuple(ProjectFile(**file) for file in value["files"])
            for file in files:
                self.w.secrets.reject(file.path)
                self.w.secrets.reject(file.content)
            await self.operation(W.WRITE, files=files)
            await self.record(
                "files_changed",
                "completed",
                "Файлы записаны",
                {
                    "created": sorted(f.path for f in files if f.path not in before),
                    "modified": sorted(
                        f.path
                        for f in files
                        if f.path in before and f.content != before[f.path]
                    ),
                },
            )
        except (TypeError, ValueError):
            raise DevelopmentFailure("invalid_model_result") from None

    async def repository_operation(self, action: str, commit_id=None):
        self.checkpoint()
        request = GitRequest(
            self.task_id,
            self.request.repository_url,
            self.request.base_branch,
            self.branch,
            commit_id,
        )
        started = time.perf_counter()
        facts = {
            "repository_url": request.repository_url,
            "base_branch": request.base_branch,
            "working_branch": self.branch,
        }
        await self.record(
            f"repository_{action}_started", "started", f"Git {action}", facts
        )
        try:
            await getattr(self.w.git, action)(self.request.agent_id, request)
        except DevelopmentFailure as error:
            await self.record(
                f"repository_{action}_finished",
                "failed",
                f"Git {action} failed",
                {**facts, "failure_code": error.code},
                closing=True,
            )
            raise
        await self.record(
            f"repository_{action}_finished",
            "completed",
            f"Git {action} completed",
            {**facts, "duration_ms": round((time.perf_counter() - started) * 1000)},
        )
        if action == "clone":
            await self.record(
                "branch_created", "completed", "Working branch created", facts
            )
        self.checkpoint()

    async def workflow(self) -> None:
        if (
            self.request.repository_url
            and self.w.service.get(self.task_id).state is S.CREATED
        ):
            await self.stage(S.CLONING_REPOSITORY)
            await self.repository_operation("clone")
        await self.stage(S.ANALYZING_REQUIREMENTS)
        context = (
            {"files": [asdict(f) for f in await self.files()]}
            if self.request.repository_url
            else {}
        )
        value = await self.reason(context)
        questions = self.strings(value.get("questions"), 5, empty=True)
        await self.record(
            "requirements_result",
            "completed",
            "Требования проверены",
            {"sufficient": not questions, "questions_count": len(questions)},
        )
        if questions:
            if (
                self.w.service.get(self.task_id).transitions.count(
                    S.ANALYZING_REQUIREMENTS
                )
                >= 3
            ):
                raise DevelopmentFailure("clarification_required")
            await self.stage(
                S.WAITING_FOR_CLARIFICATION, questions=questions, clarification=""
            )
            return
        await self.stage(S.PLANNING, questions=())
        plan = self.strings((await self.reason(context)).get("steps"), 12)
        await self.record(
            "plan_ready", "completed", "План подготовлен", {"steps": len(plan)}
        )
        await self.stage(S.PREPARING_WORKSPACE, plan=plan)
        await self.operation(W.PREPARE)
        if self.request.repository_url is None:
            await self.operation(W.INIT)
        await self.stage(S.IMPLEMENTING)
        await self.implement()
        # Code-only mode: skip build, tests, review and fixing.
        checks = ()
        await self.stage(S.COMMITTING)
        files = await self.files()
        await self.operation(W.STATUS)
        await self.operation(W.ADD, paths=tuple(f.path for f in files))
        diff = await self.operation(W.DIFF)
        self.w.secrets.reject(diff.output)
        commit = await self.operation(W.COMMIT)
        if not isinstance(commit.commit_id, str):
            raise DevelopmentFailure("operation_failed")
        result = DevelopmentResult(
            self.branch,
            commit.commit_id,
            tuple(f.path for f in files),
            checks,
            None,
            False,
            self.w.workspace.execution_backend,
            self.request.repository_url,
            self.request.base_branch if self.request.repository_url else None,
            self.branch,
        )
        await self.record(
            "git_commit", "completed", "Коммит создан", {"commit_id": result.commit_id}
        )
        if self.request.publish:
            await self.stage(S.PUSHING)
            await self.repository_operation("push", commit.commit_id)
            result = replace(result, published=True)
        await self.stage(S.COMPLETED, result=result)

    async def execute(self) -> tuple[Message, ...]:
        try:
            await self.workflow()
            current = self.w.service.get(self.task_id)
            self.text = (
                "Нужно уточнить требования. Вопросы доступны в карточке задачи."
                if current.state is S.WAITING_FOR_CLARIFICATION
                else "Готово. Изменения записаны, коммит создан и задача завершена."
            )
            if (
                current.result is not None
                and current.result.execution_backend != "agent"
            ):
                self.text += " Проверка выполнена через тестовый адаптер."
            self.text = observable_text(self.w.secrets.redact(self.text))
            now = datetime.now(UTC)
            return (
                Message(
                    uuid4().hex, self.turn_id, 1, "user", "Run development task", now
                ),
                Message(self.message_id, self.turn_id, 2, "assistant", self.text, now),
            )
        except Exception as error:  # noqa: BLE001 - expose only stable failure codes
            code = (
                error.code
                if isinstance(error, DevelopmentFailure)
                else "operation_failed"
            )
            await self.finish_step("cancelled" if code == "cancelled" else "failed")
            task = self.w.service.get(self.task_id)
            if task.state not in TERMINAL_STATES:
                self.w.service.transition(
                    self.task_id,
                    S.CANCELLED if code == "cancelled" else S.FAILED,
                    failure_code=code,
                )
            self.phase, self.attempt, self.step_name = "task", 0, None
            await self.record(
                "task_status",
                "cancelled" if code == "cancelled" else "failed",
                "Задача остановлена",
                {
                    "state": self.w.service.get(self.task_id).state.value,
                    "failure_code": code,
                },
                closing=True,
            )
            raise AgentLifecycleFailure(
                AgentLifecycleOperation.MESSAGE,
                AgentLifecycleErrorCode.INTERACTION_FAILED,
                agent_id=self.request.agent_id,
            ) from None
        finally:
            self.w.service.release(self.task_id)
