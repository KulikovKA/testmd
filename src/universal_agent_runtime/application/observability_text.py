"""Conservative debug projection, layered on the shared secret redactor."""

import re

from universal_agent_runtime.application.text_stream import redact_text


def observable_text(text: str, secrets: tuple[str, ...] = ()) -> str:
    text = redact_text(text, secrets)
    # Debug text is optional. Suppress an entire value if it could contain
    # reasoning, environment dumps, credential assignments or certificate data.
    if re.search(
        r"<(?:think|thinking|reasoning|analysis)\b|\[/?(?:think|reasoning)\]|<\|(?:analysis|think)\|>"
        r"|-----BEGIN [A-Z ]*(?:CERTIFICATE|PRIVATE KEY)-----"
        r"|(?:authorization|proxy-authorization|cookie|set-cookie)\s*[\"']?\s*[:=]"
        r"|(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|username|secret|credentials|environment|env)\s*[\"']?\s*[:=]"
        r"|(?:OPENAI_API_KEY|SFERA_USERNAME|SFERA_PASSWORD|REPOSITORY_TOKEN)\b"
        r"|https?://[^\s/@]+(?::[^\s/@]*)?@"
        r"|(?:^|[\s/\\\"'])\.env(?:$|[\s\"':=])",
        text,
        re.IGNORECASE,
    ) or re.search(r"\b[A-Z][A-Z0-9_]{2,}\s*=", text):
        return "[REDACTED_DEBUG_CONTENT]"
    return text.replace("\x00", "")
