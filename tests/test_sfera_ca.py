from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from universal_agent_runtime.adapters.docker_agent_qwen import DockerAgentQwenRunner
from universal_agent_runtime.adapters.qwen_session import (
    QwenSessionAdapter,
    QwenSessionConfig,
)
from universal_agent_runtime.application.ports.interaction_values import (
    SessionReference,
)
from universal_agent_runtime.domain.identifiers import AgentId, SessionId


class SferaCertificatePlumbingTests(unittest.TestCase):
    def test_ca_is_copied_to_agent_workspace_and_exposed_only_to_task_turns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            certificate = root / "corporate-ca.pem"
            contents = b"-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
            certificate.write_bytes(contents)
            config = QwenSessionConfig(
                storage_root=root / "sessions",
                base_url="http://10.21.171.2:11434/v1",
                model="qwen-3.8-multimodal:latest",
                api_key="test-placeholder",
                sfera_base_url="https://sfera.ai.dev.sfera-t1.ru",
                sfera_username="sfera-user",
                sfera_password="sfera-password",
                sfera_ca_cert_path=certificate,
            )
            reference = SessionReference(
                AgentId("agent-one"), SessionId("session-one")
            )
            adapter = QwenSessionAdapter(config, runner=object())

            asyncio.run(adapter.create_session(reference))

            copied = (
                root
                / "sessions"
                / "agent-one"
                / "workspace"
                / ".uar-tools"
                / "sfera-ca.pem"
            )
            self.assertEqual(copied.read_bytes(), contents)
            runner = DockerAgentQwenRunner(
                config,
                workspace="/workspace",
                user="10001:10001",
                client=object(),
            )
            environment = runner._task_environment(("get_task",))
            self.assertEqual(
                environment["NODE_EXTRA_CA_CERTS"],
                "/workspace/.uar-tools/sfera-ca.pem",
            )
            self.assertNotIn("NODE_EXTRA_CA_CERTS", runner._task_environment(()))
            default_trust_runner = DockerAgentQwenRunner(
                replace(config, sfera_ca_cert_path=None),
                workspace="/workspace",
                user="10001:10001",
                client=object(),
            )
            self.assertNotIn(
                "NODE_EXTRA_CA_CERTS",
                default_trust_runner._task_environment(("get_task",)),
            )


if __name__ == "__main__":
    unittest.main()
