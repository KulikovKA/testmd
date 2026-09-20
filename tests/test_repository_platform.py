import tempfile
import unittest
from pathlib import Path

from universal_agent_runtime.adapters.fake_repository_platform import (
    FakeRepositoryPlatformAdapter,
)
from universal_agent_runtime.adapters.sfera_code_repository import (
    SferaCodeRepositoryAdapter,
)
from universal_agent_runtime.application.ports.repository_platform import (
    CloneInformation,
    CreateRepositoryRequest,
    RepositoryMetadata,
    validate_https_url,
)
from universal_agent_runtime.domain.development_task import DevelopmentFailure


class RepositoryContractTests(unittest.TestCase):
    def test_metadata_and_clone_information_are_provider_neutral(self):
        value = RepositoryMetadata(
            "42", "group/team", "project", web_url="https://code.example/group/project"
        )
        clone = CloneInformation(
            value.repository_id,
            "https://code.example/group/project.git",
            credential_reference="repo-access",
        )
        self.assertEqual(value.namespace, "group/team")
        self.assertEqual(clone.credential_reference, "repo-access")
        self.assertEqual(
            CreateRepositoryRequest("team", "project", "request-1").default_branch,
            "main",
        )

    def test_credentials_and_untrusted_transport_are_not_clone_urls(self):
        for url in (
            "https://user:token@code.example/r.git",
            "https://code.example/r.git?token=secret",
            "ssh://git@code.example/r",
            "git@code.example:r",
            "file:///tmp/r",
            "ext::sh -c command",
            "http://code.example/r",
            "https://code.example/a/../r",
            "https://code.example/r#token",
            "https://code.example:0/r",
            "https://code.example/\nr",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_https_url(url)


class FakeRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_idempotency_and_clone_information_without_external_service(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = FakeRepositoryPlatformAdapter(Path(directory))
            request = CreateRepositoryRequest("team", "project", "request")
            first = await adapter.create_repository(request)
            self.assertEqual(await adapter.create_repository(request), first)
            self.assertEqual(await adapter.get_repository(first.repository_id), first)
            clone = await adapter.get_clone_information(first.repository_id)
            self.assertEqual(Path(clone.location).parent, Path(directory).resolve())
            self.assertIsNone(clone.credential_reference)
            self.assertEqual(list(Path(directory).iterdir()), [])

    async def test_collision_and_changed_idempotency_request_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = FakeRepositoryPlatformAdapter(Path(directory))
            await adapter.create_repository(
                CreateRepositoryRequest("team", "project", "request")
            )
            for request in (
                CreateRepositoryRequest("team", "project", "other"),
                CreateRepositoryRequest("team", "changed", "request"),
            ):
                with self.assertRaises(DevelopmentFailure):
                    await adapter.create_repository(request)
            with self.assertRaises(DevelopmentFailure):
                await adapter.get_repository("missing")

    async def test_sfera_skeleton_never_invents_transport(self):
        adapter = SferaCodeRepositoryAdapter()
        for action in (
            adapter.create_repository(
                CreateRepositoryRequest("team", "project", "request")
            ),
            adapter.get_repository("repo"),
            adapter.get_clone_information("repo"),
        ):
            with self.assertRaises(DevelopmentFailure) as raised:
                await action
            self.assertEqual(raised.exception.code, "repository_unavailable")
