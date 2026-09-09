"""Explicit dependency composition for the Agent Orchestrator HTTP application."""

import inspect
from dataclasses import dataclass

from universal_agent_runtime.adapters.docker_agent_qwen import DockerAgentQwenRunner
from universal_agent_runtime.adapters.docker_runtime import (
    DockerRuntime,
    DockerWorkload,
)
from universal_agent_runtime.adapters.in_memory_agent_repository import (
    InMemoryAgentRepository,
)
from universal_agent_runtime.adapters.qwen_session import (
    QwenSessionAdapter,
    QwenSessionConfig,
)
from universal_agent_runtime.application.agent_chat import (
    AgentChatService,
    ChatConfiguration,
)
from universal_agent_runtime.application.agent_lifecycle import (
    AgentLifecycleService,
    LifecycleConfiguration,
)
from universal_agent_runtime.application.ports.agent_interaction import AgentInteraction
from universal_agent_runtime.application.ports.agent_repository import AgentRepository
from universal_agent_runtime.application.ports.agent_runtime import AgentRuntime
from universal_agent_runtime.application.ports.runtime_values import (
    EnvironmentVariable,
    NetworkDestination,
    OperationOptions,
    ResourceLimits,
    SecretBinding,
)
from universal_agent_runtime.configuration import ApplicationSettings, RuntimeDriver


@dataclass(frozen=True)
class ApplicationComposition:
    """Explicit application-lifetime dependencies; no process-global state."""

    settings: ApplicationSettings | None = None
    runtime: AgentRuntime | None = None
    interaction: AgentInteraction | None = None
    lifecycle: AgentLifecycleService | None = None
    chat: AgentChatService | None = None
    package_name: str = "universal_agent_runtime"

    async def close(self) -> None:
        """Close owned adapters once the application lifespan ends."""

        closed: set[int] = set()
        if self.chat is not None:
            await self.chat.close()
        for dependency in (self.interaction, self.runtime):
            if dependency is None or id(dependency) in closed:
                continue
            closed.add(id(dependency))
            closer = getattr(dependency, "close", None)
            if callable(closer):
                outcome = closer()
                if inspect.isawaitable(outcome):
                    await outcome


def create_composition() -> ApplicationComposition:
    """Return the legacy inert marker without reading configuration or starting I/O."""
    return ApplicationComposition()


def compose_application(
    settings: ApplicationSettings,
    *,
    runtime: AgentRuntime | None = None,
    interaction: AgentInteraction | None = None,
    repository: AgentRepository | None = None,
) -> ApplicationComposition:
    """Select deployment adapters at the composition root or accept explicit fakes."""

    if runtime is None:
        runtime = _compose_runtime(settings)
    if interaction is None:
        interaction = _compose_interaction(settings)
    if repository is None:
        repository = InMemoryAgentRepository()
    lifecycle = _compose_lifecycle(settings, runtime, interaction, repository)
    chat = AgentChatService(
        interaction,
        repository,
        ChatConfiguration(
            max_message_characters=settings.chat_max_message_characters,
            max_response_characters=settings.chat_max_response_characters,
            max_history_messages=settings.chat_max_history_messages,
            max_history_page_size=settings.chat_max_history_page_size,
            redacted_values=(settings.qwen_api_key,),
        ),
    )
    return ApplicationComposition(settings, runtime, interaction, lifecycle, chat)


def _network_destinations(
    settings: ApplicationSettings,
) -> tuple[NetworkDestination, ...]:
    if settings.docker_network_mode != "bridge":
        return ()
    assert settings.docker_network_host is not None
    assert settings.docker_network_port is not None
    return (
        NetworkDestination(settings.docker_network_host, settings.docker_network_port),
    )


def _compose_runtime(settings: ApplicationSettings) -> AgentRuntime:
    if settings.runtime_driver is not RuntimeDriver.DOCKER:
        raise ValueError("unsupported runtime driver")
    destinations = _network_destinations(settings)
    healthcheck = {
        "test": ["CMD", "agent-runtime", "readiness"],
        "interval": int(settings.docker_healthcheck_interval_seconds * 1_000_000_000),
        "timeout": int(settings.docker_healthcheck_timeout_seconds * 1_000_000_000),
        "retries": settings.docker_healthcheck_retries,
        "start_period": int(
            settings.docker_healthcheck_start_period_seconds * 1_000_000_000
        ),
    }
    workload = DockerWorkload(
        settings.docker_image,
        settings.docker_command,
        settings.docker_user,
        settings.docker_workspace_target,
        healthcheck=healthcheck,
        network_mode=settings.docker_network_mode,
        network_destinations=destinations,
    )

    def resolve_secret(secret_id: str) -> str:
        if secret_id != settings.qwen_api_key_secret_id:
            raise KeyError("unknown secret reference")
        return settings.qwen_api_key

    return DockerRuntime(
        {settings.docker_workload_key: workload}, secret_resolver=resolve_secret
    )


def _compose_interaction(settings: ApplicationSettings) -> AgentInteraction:
    config = QwenSessionConfig(
        storage_root=settings.qwen_storage_root,
        base_url=settings.qwen_base_url,
        model=settings.qwen_model,
        api_key=settings.qwen_api_key,
    )
    return QwenSessionAdapter(
        config,
        runner=DockerAgentQwenRunner(
            config,
            workspace=settings.docker_workspace_target,
            user=settings.docker_user,
        ),
    )


def _compose_lifecycle(
    settings: ApplicationSettings,
    runtime: AgentRuntime,
    interaction: AgentInteraction,
    repository: AgentRepository,
) -> AgentLifecycleService:
    return AgentLifecycleService(
        runtime,
        interaction,
        repository,
        LifecycleConfiguration(
            workload=settings.docker_workload_key,
            resources=ResourceLimits(
                settings.agent_cpu_cores, settings.agent_memory_bytes
            ),
            environment=(
                EnvironmentVariable("QWEN_OLLAMA_BASE_URL", settings.qwen_base_url),
                EnvironmentVariable("QWEN_OLLAMA_MODEL", settings.qwen_model),
            ),
            secrets=(SecretBinding("OPENAI_API_KEY", settings.qwen_api_key_secret_id),),
            network=_network_destinations(settings),
            operation_options=OperationOptions(
                settings.agent_operation_timeout_seconds
            ),
            readiness_timeout_seconds=settings.agent_readiness_timeout_seconds,
            readiness_poll_interval_seconds=(
                settings.agent_readiness_poll_interval_seconds
            ),
        ),
    )
