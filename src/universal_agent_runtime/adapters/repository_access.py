"""Fail-closed credential boundary; production never injects Git secrets into Agent."""

import re
from pathlib import Path
from urllib.parse import urlsplit

from universal_agent_runtime.application.ports.repository_credentials import (
    RepositoryAccess,
    RepositoryAction,
)
from universal_agent_runtime.application.ports.repository_platform import (
    CloneInformation,
    CloneTransport,
    validate_https_url,
)
from universal_agent_runtime.application.text_stream import redact_text
from universal_agent_runtime.domain.development_task import (
    DevelopmentFailure,
    validate_branch,
)


class PublicRepositoryCredentialAdapter:
    def __init__(
        self,
        allowed_hosts: tuple[str, ...] = (),
        *,
        local_test_root: Path | None = None,
    ) -> None:
        self._allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self._local_test_root = (
            local_test_root.resolve() if local_test_root is not None else None
        )
        if self._local_test_root is not None and self._local_test_root == Path(
            self._local_test_root.anchor
        ):
            raise ValueError("test root must not be filesystem root")

    def authorize(
        self,
        clone: CloneInformation,
        *,
        branch: str,
        action: RepositoryAction,
        publish_authorized: bool = False,
    ) -> RepositoryAccess:
        validate_branch(branch)
        if not isinstance(action, RepositoryAction):
            raise DevelopmentFailure("publication_rejected")
        if clone.credential_reference is not None:
            # Authentication needs a trusted broker outside the untrusted build UID.
            raise DevelopmentFailure("credential_unavailable")
        if action is RepositoryAction.PUSH and not publish_authorized:
            raise DevelopmentFailure("publication_rejected")
        if clone.transport is CloneTransport.LOCAL_TEST:
            try:
                path = Path(clone.location).resolve(strict=True)
                if (
                    self._local_test_root is None
                    or not path.is_relative_to(self._local_test_root)
                    or path == self._local_test_root
                    or not path.is_dir()
                ):
                    raise DevelopmentFailure("publication_rejected")
            except (OSError, ValueError):
                raise DevelopmentFailure("publication_rejected") from None
            location = str(path)
        else:
            location = validate_https_url(clone.location)
            parsed = urlsplit(location)
            if parsed.hostname not in self._allowed_hosts or parsed.port not in (
                None,
                443,
            ):
                raise DevelopmentFailure("publication_rejected")
        return RepositoryAccess(
            clone.repository_id, location, clone.transport, branch, action
        )


class ConfiguredSecretPolicy:
    """Reuse the accepted redactor and reject secret-bearing model/file data."""

    def __init__(self, values: tuple[str, ...] = ()) -> None:
        self._values = tuple(value for value in values if value)

    def redact(self, text: str) -> str:
        # Credential-bearing URLs are never useful public diagnostics.
        text = re.sub(r"https?://[^\s/@]+(?::[^\s/@]*)?@[^\s]+", "[REDACTED_URL]", text)
        return redact_text(text, self._values)

    def reject(self, text: str) -> None:
        if self.redact(text) != text:
            raise DevelopmentFailure("secret_rejected")

    def __repr__(self) -> str:
        return "ConfiguredSecretPolicy()"
