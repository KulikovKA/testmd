"""Closed helper entrypoint. Runs only in its own disposable credential container.

Push imports objects into a fresh repository: Agent config, hooks, refs, indexes,
alternates and attributes are never interpreted by credential-bearing Git.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from universal_agent_runtime.application.ports.trusted_git import GitRequest
from universal_agent_runtime.domain.development_task import DevelopmentFailure


def safe_directory(path: Path) -> Path:
    for item in (*reversed(path.parents), path):
        if item.is_symlink() or (item.exists() and not item.is_dir()):
            raise DevelopmentFailure("workspace_rejected")
    return path


def copy_tree(source: Path, destination: Path, *, objects=False) -> None:
    """Bounded physical copy; never follow links or Git object alternates."""
    safe_directory(source)
    count, size = 0, 0
    for parent, directories, files in os.walk(source, followlinks=False):
        relative = Path(parent).relative_to(source)
        if objects and relative == Path("."):
            directories[:] = [d for d in directories if d != "info"]
        for name in directories + files:
            item = Path(parent) / name
            info = item.lstat()
            count += 1
            size += info.st_size
            if (
                count > 20000
                or size > 256 * 1024 * 1024
                or stat.S_ISLNK(info.st_mode)
                or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
            ):
                raise DevelopmentFailure("workspace_rejected")
            if objects and not (
                (
                    stat.S_ISDIR(info.st_mode)
                    and relative == Path(".")
                    and re.fullmatch(r"[0-9a-f]{2}|pack", name)
                )
                or (
                    relative.name == "pack"
                    and re.fullmatch(r"pack-[0-9a-f]{40,64}\.(pack|idx|rev)", name)
                )
                or (
                    re.fullmatch(r"[0-9a-f]{2}", str(relative))
                    and re.fullmatch(r"[0-9a-f]{38}|[0-9a-f]{62}", name)
                )
            ):
                raise DevelopmentFailure("workspace_rejected")
            target = destination / relative / name
            if stat.S_ISDIR(info.st_mode):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(item, target)
                target.chmod(info.st_mode & 0o777)


def git_failure(stderr: str, fallback: str) -> DevelopmentFailure:
    text = stderr.lower()
    code = fallback
    if "permission denied" in text or "authentication failed" in text:
        code = "repository_auth_failed"
    elif (
        "host key verification failed" in text
        or "connection refused" in text
        or "could not resolve" in text
    ):
        code = "repository_unavailable"
    elif (
        "remote branch" in text
        and "not found" in text
        or "couldn't find remote ref" in text
    ):
        code = "repository_branch_not_found"
    elif (
        "repository not found" in text
        or "does not appear to be a git repository" in text
    ):
        code = "repository_not_found"
    elif "non-fast-forward" in text or "[rejected]" in text:
        code = "repository_conflict"
    return DevelopmentFailure(code)


class GitCommands:
    def __init__(self, *, local_test=False):
        self.local_test = local_test

    def __call__(self, cwd, *args, failure="repository_clone_failed"):
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(cwd),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ATTR_NOSYSTEM": "1",
            "LC_ALL": "C",
            "GIT_SSH_VARIANT": "ssh",
        }
        if os.name == "nt":
            environment["SystemRoot"] = os.environ.get("SystemRoot", "C:/Windows")
        if not self.local_test:
            environment["GIT_SSH_COMMAND"] = (
                "ssh -F /dev/null -i /run/uar/key -o IdentitiesOnly=yes -o IdentityAgent=none "
                "-o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/run/uar/known_hosts "
                "-o GlobalKnownHostsFile=/dev/null -o ForwardAgent=no -o ClearAllForwardings=yes "
                "-o ConnectTimeout=15"
            )
        command = [
            "git",
            "-c",
            "core.hooksPath=/nonexistent-uar-hooks",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "credential.helper=",
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.ssh.allow=always",
            "-c",
            "init.templateDir=",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.symlinks=true",
        ]
        if self.local_test:
            command += ["-c", "protocol.file.allow=always"]
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            try:
                completed = subprocess.run(
                    command + list(args),
                    cwd=cwd,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=errors,
                    timeout=90,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise DevelopmentFailure("repository_unavailable") from None
            errors.seek(0)
            diagnostic = errors.read(65536).decode("utf-8", errors="replace")
            output.seek(0)
            stdout = output.read(65536).decode("utf-8", errors="replace").strip()
            if completed.returncode:
                raise git_failure(diagnostic + "\n" + stdout, failure)
            return stdout


def execute(
    operation: str,
    request: GitRequest,
    root: Path,
    *,
    git=None,
    remote=None,
    owner=None,
):
    """Test injection is Python-only; CLI cannot supply local paths or commands."""
    if operation not in {"clone", "push"}:
        raise DevelopmentFailure("invalid_request")
    git = git or GitCommands()
    remote = remote or request.repository_url
    project = safe_directory(root / "projects" / request.task_id)
    with tempfile.TemporaryDirectory(prefix="uar-git-") as directory:
        temporary = Path(directory)
        clean = temporary / "repo"
        if operation == "clone":
            if project.exists() and any(project.iterdir()):
                raise DevelopmentFailure("repository_conflict")
            git(
                temporary,
                "clone",
                "--no-local",
                "--single-branch",
                "--branch",
                request.base_branch,
                "--",
                remote,
                str(clean),
            )
            git(clean, "checkout", "-b", request.working_branch)
            project.mkdir(parents=True, exist_ok=True)
            copy_tree(clean, project)
            if owner is not None:
                os.chown(project.parent, owner, owner)
                for parent, directories, files in os.walk(project):
                    os.chown(parent, owner, owner)
                    for name in files:
                        os.chown(Path(parent) / name, owner, owner)
        else:
            if request.commit_id is None:
                raise DevelopmentFailure("repository_conflict")
            git(temporary, "init", "--bare", str(clean))
            # Never run Git in the Agent-owned repository, even while paused.
            copy_tree(project / ".git" / "objects", clean / "objects", objects=True)
            git(
                clean,
                "cat-file",
                "-e",
                request.commit_id + "^{commit}",
                failure="repository_push_failed",
            )
            git(
                clean,
                "fetch",
                "--no-tags",
                "--",
                remote,
                "refs/heads/" + request.base_branch,
                failure="repository_push_failed",
            )
            git(
                clean,
                "merge-base",
                "--is-ancestor",
                "FETCH_HEAD",
                request.commit_id,
                failure="repository_conflict",
            )
            git(
                clean,
                "push",
                "--porcelain",
                "--",
                remote,
                request.commit_id + ":refs/heads/" + request.working_branch,
                failure="repository_push_failed",
            )


def main():
    try:
        if len(sys.argv) != 3 or len(sys.argv[2]) > 4096:
            raise DevelopmentFailure("invalid_request")
        execute(
            sys.argv[1],
            GitRequest(**json.loads(sys.argv[2])),
            Path("/workspace"),
            owner=10001,
        )
        print(json.dumps({"success": True}))
    except Exception as error:  # noqa: BLE001 - closed helper protocol
        code = (
            error.code
            if isinstance(error, DevelopmentFailure)
            else "repository_unavailable"
        )
        print(json.dumps({"success": False, "error": code}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
