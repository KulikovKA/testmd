import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest

from universal_agent_runtime.adapters.qwen_session import (
    QwenSessionAdapter,
    QwenSessionConfig,
)
from universal_agent_runtime.application.ports.interaction_values import (
    SessionReference,
    TurnRequest,
)
from universal_agent_runtime.domain.identifiers import AgentId, SessionId


@pytest.mark.skipif(
    os.getenv("RUN_QWEN_OLLAMA_INTEGRATION") != "1",
    reason="requires the explicitly configured local Docker and Ollama services",
)
def test_qwen_session_survives_new_process_and_adapter(tmp_path: Path) -> None:
    async def scenario() -> None:
        reference = SessionReference(
            AgentId(f"live-agent-{uuid4().hex[:8]}"),
            SessionId(f"live-session-{uuid4().hex[:8]}"),
        )
        codeword = f"ORBIT_{uuid4().hex[:12].upper()}"
        config = QwenSessionConfig(
            storage_root=tmp_path / "sessions",
            base_url=os.getenv(
                "QWEN_OLLAMA_BASE_URL",
                "http://host.docker.internal:11434/v1",
            ),
            model=os.getenv("QWEN_OLLAMA_MODEL", "qwen3:1.7b"),
            api_key=os.getenv("QWEN_OLLAMA_API_KEY", "ollama"),
        )
        first_adapter = QwenSessionAdapter(config)
        await first_adapter.create_session(reference)
        try:
            first = await first_adapter.turn(
                TurnRequest(
                    reference,
                    f"Remember the exact codeword {codeword}. Reply only ACK.",
                )
            )
            assert first.completed_turns == 1
        finally:
            await first_adapter.close()

        reopened_adapter = QwenSessionAdapter(config)
        try:
            observation = await reopened_adapter.create_session(reference)
            second = await reopened_adapter.turn(
                TurnRequest(
                    reference,
                    "What exact codeword did I ask you to remember? "
                    "Reply with only that codeword. Do not reply ACK.",
                )
            )
            assert observation.completed_turns == 1
            assert second.completed_turns == 2
            assert codeword in second.response
            await reopened_adapter.delete_session(reference)
            assert not (config.storage_root / reference.agent_id.value).exists()
        finally:
            await reopened_adapter.close()

    asyncio.run(scenario())
