"""Real TCP SSE delivery through Uvicorn, Docker Qwen, and external Ollama."""

import json
import os
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import pytest
import uvicorn

from tests.integration.test_agent_lifecycle_api import _build_agent_image, _environment
from universal_agent_runtime.composition import compose_application
from universal_agent_runtime.configuration import ApplicationSettings
from universal_agent_runtime.http_api import create_application


@pytest.mark.skipif(
    os.getenv("RUN_QWEN_OLLAMA_INTEGRATION") != "1",
    reason="requires explicitly configured Docker/Ollama",
)
def test_live_sse_starts_before_committed_content_and_shares_json_session(
    tmp_path: Path,
) -> None:
    _build_agent_image()
    environment = _environment(tmp_path)
    endpoint = os.getenv("QWEN_OLLAMA_BASE_URL", environment["UAR_QWEN_BASE_URL"])
    parsed = urlparse(endpoint)
    environment.update(
        {
            "UAR_DOCKER_NETWORK_MODE": "bridge",
            "UAR_DOCKER_NETWORK_HOST": str(parsed.hostname),
            "UAR_DOCKER_NETWORK_PORT": str(
                parsed.port or (443 if parsed.scheme == "https" else 80)
            ),
            "UAR_QWEN_BASE_URL": endpoint,
            "UAR_QWEN_MODEL": os.getenv("QWEN_OLLAMA_MODEL", "qwen3:1.7b"),
            "UAR_QWEN_API_KEY": os.getenv("QWEN_OLLAMA_API_KEY", "ollama"),
            "UAR_STREAM_HEARTBEAT_SECONDS": "0.1",
        }
    )
    settings = ApplicationSettings.from_environment(environment)
    app = create_application(compose_application(settings))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    agent_id: str | None = None
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=420) as client:
            try:
                created = client.post("/agents", json={"request_id": uuid4().hex})
                assert created.status_code == 201
                agent_id = created.json()["agent_id"]
                path = f"/agents/{agent_id}"
                assert client.post(path + "/start").status_code == 200
                codeword = "ORBIT_" + uuid4().hex[:12].upper()
                events = []
                keepalives = 0
                with client.stream(
                    "POST",
                    path + "/messages/stream",
                    json={
                        "content": f"Remember the exact codeword {codeword}. Reply only ACK."
                    },
                ) as response:
                    assert response.status_code == 200
                    for line in response.iter_lines():
                        if line == ": keep-alive":
                            keepalives += 1
                        if not line.startswith("data: "):
                            continue
                        event = json.loads(line[6:])
                        events.append(event)
                        if event["data"]["type"] == "started":
                            # A separate live HTTP request proves early delivery,
                            # not TestClient buffering or fabricated token chunks.
                            assert client.get(path).json()["state"] == "BUSY"
                            assert (
                                client.get(path + "/messages").json()["messages"] == []
                            )
                assert [e["data"]["type"] for e in events] == [
                    "started",
                    "content",
                    "completed",
                ]
                assert keepalives > 0
                assert [e["sequence"] for e in events] == [1, 2, 3]
                history = client.get(path + "/messages").json()["messages"]
                assert events[1]["data"]["content"] == history[-1]["content"]
                assert history[0]["turn_id"] == events[0]["turn_id"]
                second = client.post(
                    path + "/messages",
                    json={
                        "content": "What exact codeword did I ask you to remember? Reply with only that codeword. Do not reply ACK."
                    },
                )
                assert second.status_code == 201, second.text
                assert codeword in second.json()["messages"][-1]["content"]
                assert (
                    client.get(path).json()["session_id"]
                    == created.json()["session_id"]
                )
            finally:
                if agent_id is not None:
                    client.post(f"/agents/{agent_id}/stop")
                    assert client.delete(f"/agents/{agent_id}").status_code == 204
                    assert not (settings.qwen_storage_root / agent_id).exists()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
