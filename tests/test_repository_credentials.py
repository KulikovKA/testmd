import tempfile
import unittest
from pathlib import Path

from universal_agent_runtime.adapters.repository_access import (
    ConfiguredSecretPolicy,
    PublicRepositoryCredentialAdapter,
)
from universal_agent_runtime.application.ports.repository_credentials import (
    RepositoryAction as A,
)
from universal_agent_runtime.application.ports.repository_platform import (
    CloneInformation,
    CloneTransport,
)
from universal_agent_runtime.domain.development_task import DevelopmentFailure


class RepositoryCredentialTests(unittest.TestCase):
    def test_private_access_fails_before_any_execution_or_credential_resolution(self):
        adapter = PublicRepositoryCredentialAdapter(("code.example",))
        with self.assertRaises(DevelopmentFailure) as raised:
            adapter.authorize(
                CloneInformation(
                    "repo",
                    "https://code.example/repo.git",
                    credential_reference="protected-reference",
                ),
                branch="main",
                action=A.CLONE,
            )
        self.assertEqual(raised.exception.code, "credential_unavailable")
        self.assertNotIn("protected-reference", str(raised.exception))

    def test_remote_host_port_and_explicit_push_authority_are_checked(self):
        adapter = PublicRepositoryCredentialAdapter(("code.example",))
        clone = CloneInformation("repo", "https://code.example/repo.git")
        self.assertEqual(
            adapter.authorize(clone, branch="main", action=A.CLONE).repository_id,
            "repo",
        )
        with self.assertRaises(DevelopmentFailure):
            adapter.authorize(clone, branch="main", action=A.PUSH)
        self.assertEqual(
            adapter.authorize(
                clone, branch="main", action=A.PUSH, publish_authorized=True
            ).action,
            A.PUSH,
        )
        for location in ("https://other.example/r", "https://code.example:8443/r"):
            with self.assertRaises(DevelopmentFailure):
                adapter.authorize(
                    CloneInformation("repo", location), branch="main", action=A.CLONE
                )

    def test_local_remotes_are_only_inside_explicit_test_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = root / "remote.git"
            remote.mkdir()
            clone = CloneInformation("repo", str(remote), CloneTransport.LOCAL_TEST)
            with self.assertRaises(DevelopmentFailure):
                PublicRepositoryCredentialAdapter().authorize(
                    clone, branch="main", action=A.CLONE
                )
            adapter = PublicRepositoryCredentialAdapter(local_test_root=root)
            self.assertEqual(
                adapter.authorize(clone, branch="main", action=A.CLONE).location,
                str(remote.resolve()),
            )
            with self.assertRaises(DevelopmentFailure):
                adapter.authorize(
                    CloneInformation(
                        "repo", str(root.parent), CloneTransport.LOCAL_TEST
                    ),
                    branch="main",
                    action=A.CLONE,
                )

    def test_known_secrets_and_url_credentials_cannot_be_written_as_generated_files(
        self,
    ):
        policy = ConfiguredSecretPolicy(("ABC", "ABCDEF"))
        self.assertEqual(policy.redact("ABCDEF then ABC"), "[REDACTED] then [REDACTED]")
        for value in ("token=ABCDEF", "https://user:password@code.example/repo.git"):
            with self.assertRaises(DevelopmentFailure):
                policy.reject(value)
        self.assertNotIn("ABC", repr(policy))
        policy.reject("public class Calculator {}")
