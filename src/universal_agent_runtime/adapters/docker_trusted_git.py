"""Own disposable helpers; private key files are never read by the orchestrator."""

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

from docker.errors import NotFound

from universal_agent_runtime.application.ports.trusted_git import (
    GitRequest,
    validate_repository_url,
)
from universal_agent_runtime.domain.development_task import DevelopmentFailure


@dataclass(frozen=True)
class TrustedGitSettings:
    private_key_file: str | None = field(default=None, repr=False)
    known_hosts_file: str | None = field(default=None, repr=False)
    allowed_endpoints: tuple[str, ...] = ()
    image: str = "uar-git-helper:local"

    def __post_init__(self):
        if any(
            re.fullmatch(r"[a-z0-9][a-z0-9.-]*:[0-9]{1,5}", e) is None
            or not 1 <= int(e.rsplit(":", 1)[1]) <= 65535
            for e in self.allowed_endpoints
        ):
            raise ValueError("invalid trusted Git endpoint configuration")
        for value in (self.private_key_file, self.known_hosts_file):
            if value is not None and (
                not Path(value).is_absolute() or any(c in value for c in "\r\n\x00")
            ):
                raise ValueError("trusted Git mounts require explicit absolute paths")


class DockerTrustedGitAdapter:
    def __init__(self, client, settings: TrustedGitSettings, *, workspace="/workspace"):
        self._client, self._settings, self._workspace = client, settings, workspace

    def validate(self, repository_url):
        validate_repository_url(repository_url, self._settings.allowed_endpoints)
        if not self._settings.private_key_file or not self._settings.known_hosts_file:
            raise DevelopmentFailure("repository_unavailable")

    def _execute(self, agent_id, request, operation):
        self.validate(request.repository_url)
        helper, agent, paused = None, None, False
        helper_name = f"uar-git-{uuid4().hex}"
        create_attempted = False
        try:
            # Stat only: no key contents enter application memory.
            for value in (
                self._settings.private_key_file,
                self._settings.known_hosts_file,
            ):
                if not Path(value).is_file() or Path(value).is_symlink():
                    raise DevelopmentFailure("repository_unavailable")
            agents = self._client.containers.list(
                all=True,
                filters={
                    "label": [
                        "io.universal-agent-runtime.managed=true",
                        f"io.universal-agent-runtime.agent={agent_id.value}",
                    ]
                },
            )
            if len(agents) != 1 or agents[0].status != "running":
                raise DevelopmentFailure("agent_unavailable")
            agent = agents[0]
            mounts = [
                m
                for m in agent.attrs.get("Mounts", [])
                if m.get("Destination") == self._workspace and m.get("Type") == "volume"
            ]
            if len(mounts) != 1:
                raise DevelopmentFailure("workspace_rejected")
            # Freeze untrusted processes before exposing the shared volume to helper.
            # Credentials still live in a separate mount/PID namespace.
            agent.pause()
            paused = True
            create_attempted = True
            helper = self._client.containers.create(
                self._settings.image,
                [operation, json.dumps(asdict(request))],
                name=helper_name,
                labels={"io.universal-agent-runtime.git-helper": "true"},
                user="0:0",
                read_only=True,
                network_mode="bridge",
                cap_drop=["ALL"],
                cap_add=["CHOWN", "DAC_OVERRIDE", "FOWNER"],
                security_opt=["no-new-privileges:true"],
                pids_limit=64,
                mem_limit="512m",
                nano_cpus=1_000_000_000,
                tmpfs={"/tmp": "rw,nosuid,nodev,noexec,size=384m,mode=1777"},
                volumes={
                    mounts[0]["Name"]: {
                        "bind": "/workspace",
                        "mode": "rw" if operation == "clone" else "ro",
                    },
                    self._settings.private_key_file: {
                        "bind": "/run/uar/key",
                        "mode": "ro",
                    },
                    self._settings.known_hosts_file: {
                        "bind": "/run/uar/known_hosts",
                        "mode": "ro",
                    },
                },
                log_config={
                    "type": "json-file",
                    "config": {"max-size": "64k", "max-file": "1"},
                },
            )
            helper.start()
            result = helper.wait(timeout=300)
            raw = helper.logs(stdout=True, stderr=False, tail=1)
            if len(raw) > 1024:
                raise DevelopmentFailure("repository_unavailable")
            value = json.loads(raw)
            if result["StatusCode"] != 0 or value.get("success") is not True:
                raise DevelopmentFailure(
                    value.get("error", f"repository_{operation}_failed")
                )
        except DevelopmentFailure:
            raise
        except Exception:  # noqa: BLE001 - safe boundary; never expose Docker/Git diagnostics
            raise DevelopmentFailure("repository_unavailable") from None
        finally:
            # Never resume Agent while a credential helper can still access its volume.
            if helper is None and create_attempted:
                try:
                    # Docker may have created the container before its response was lost.
                    helper = self._client.containers.get(helper_name)
                except NotFound:
                    pass
                except Exception:  # noqa: BLE001 - uncertain helper lifetime: keep Agent paused
                    raise DevelopmentFailure("repository_unavailable") from None
            if helper is not None:
                try:
                    helper.remove(force=True)
                except Exception:  # noqa: BLE001 - safe boundary; never expose Docker/Git diagnostics
                    # Fail closed: leave Agent paused if helper removal is unconfirmed.
                    raise DevelopmentFailure("repository_unavailable") from None
            if paused:
                try:
                    agent.unpause()
                except Exception:  # noqa: BLE001 - safe boundary; never expose Docker/Git diagnostics
                    raise DevelopmentFailure("repository_unavailable") from None

    async def _owned(self, agent_id, request, operation):
        task = asyncio.create_task(
            asyncio.to_thread(self._execute, agent_id, request, operation)
        )
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def clone(self, agent_id, request: GitRequest):
        await self._owned(agent_id, request, "clone")

    async def push(self, agent_id, request: GitRequest):
        await self._owned(agent_id, request, "push")

    def close(self):
        self._client.close()
