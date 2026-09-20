"""Real local Git and deterministic Java fixtures; never an external service."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from universal_agent_runtime.adapters.fake_repository_platform import (
    FakeRepositoryPlatformAdapter,
)
from universal_agent_runtime.application.ports.repository_platform import (
    CreateRepositoryRequest,
)


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    environment = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Local Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Local Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }
    return subprocess.run(
        ["git", *args],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=check,
        timeout=30,
        shell=False,
    )


@unittest.skipUnless(shutil.which("git"), "LOCAL_NOT_AVAILABLE: Git")
class LocalBareGitTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_provider_push_is_verified_by_independent_clone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = FakeRepositoryPlatformAdapter(root)
            repository = await provider.create_repository(
                CreateRepositoryRequest("team", "java-project", "create")
            )
            clone = await provider.get_clone_information(repository.repository_id)
            git(root, "init", "--bare", "--initial-branch=main", clone.location)
            git(root, "clone", "--", clone.location, "project")
            project = root / "project"
            source = project / "src/main/java/Calculator.java"
            source.parent.mkdir(parents=True)
            source.write_text(
                "public class Calculator { public static int add(int a, int b) { return a + b; } }\n",
                encoding="utf-8",
            )
            git(project, "add", "--", "src/main/java/Calculator.java")
            git(project, "commit", "-m", "Add calculator fixture")
            commit = git(project, "rev-parse", "HEAD").stdout.strip()
            git(project, "push", "origin", "HEAD:refs/heads/main")
            git(root, "clone", "--", clone.location, "independent")
            self.assertEqual(
                git(root / "independent", "rev-parse", "HEAD").stdout.strip(), commit
            )
            self.assertEqual(
                (root / "independent/src/main/java/Calculator.java").read_text(),
                source.read_text(),
            )
            self.assertEqual(git(project, "status", "--porcelain").stdout, "")

    @unittest.skipUnless(
        shutil.which("java") and shutil.which("javac"), "LOCAL_NOT_AVAILABLE: JDK"
    )
    def test_available_jdk_compiles_and_runs_deterministic_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "CalculatorTest.java"
            source.write_text(
                'public class CalculatorTest { public static void main(String[] args) { if (add(2, 3) != 5) throw new AssertionError(); System.out.println("PASS"); } static int add(int a, int b) { return a+b; } }',
                encoding="utf-8",
            )
            subprocess.run(
                ["javac", "--release", "17", "-d", str(root), str(source)],
                cwd=root,
                capture_output=True,
                check=True,
                timeout=30,
                shell=False,
            )
            result = subprocess.run(
                ["java", "-cp", str(root), "CalculatorTest"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
                shell=False,
            )
            self.assertEqual(result.stdout.strip(), "PASS")
