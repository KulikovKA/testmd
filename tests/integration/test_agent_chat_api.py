"""Opt-in public non-streaming API through the Agent's Docker runtime and Ollama."""

import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import docker
import pytest
from fastapi.testclient import TestClient

from tests.integration.test_agent_lifecycle_api import _build_agent_image, _environment
from universal_agent_runtime.composition import compose_application
from universal_agent_runtime.configuration import ApplicationSettings
from universal_agent_runtime.http_api import create_application


@pytest.mark.skipif(
    os.getenv("RUN_QWEN_OLLAMA_INTEGRATION") != "1",
    reason="requires explicitly configured Docker/Ollama services",
)
def test_real_public_chat_recalls_context_after_stop_start(tmp_path: Path) -> None:
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
        }
    )
    settings = ApplicationSettings.from_environment(environment)
    app = create_application(compose_application(settings))
    codeword = "ORBIT_" + uuid4().hex[:12].upper()
    agents: list[str] = []
    docker_client = docker.from_env()
    with TestClient(app) as client:
        try:
            for _ in range(2):
                created = client.post("/agents", json={"request_id": uuid4().hex})
                assert created.status_code == 201, created.text
                agents.append(created.json()["agent_id"])
            path = f"/agents/{agents[0]}"
            assert client.post(path + "/start").status_code == 200
            filters: dict[str, str | list[str] | bool] = {
                "label": f"io.universal-agent-runtime.agent={agents[0]}"
            }
            container = docker_client.containers.list(all=True, filters=filters)[0]
            before_id = container.id
            first = client.post(
                path + "/messages",
                json={
                    "content": f"Remember the exact codeword {codeword}. Reply only ACK."
                },
            )
            assert first.status_code == 201, first.text
            assert client.post(path + "/stop").status_code == 200
            assert (
                client.get(path + "/messages").json()["messages"]
                == first.json()["messages"]
            )
            assert client.post(path + "/start").status_code == 200
            second = client.post(
                path + "/messages",
                json={
                    "content": "What exact codeword did I ask you to remember? Reply with only that codeword. Do not reply ACK."
                },
            )
            assert second.status_code == 201, second.text
            assert codeword in second.json()["messages"][-1]["content"]
            history = client.get(path + "/messages").json()["messages"]
            assert [m["sequence"] for m in history] == [1, 2, 3, 4]
            assert len({m["message_id"] for m in history}) == 4
            assert client.get(f"/agents/{agents[1]}/messages").json()["messages"] == []
            assert client.get(path).json()["state"] == "READY"
            current = docker_client.containers.list(all=True, filters=filters)
            assert len(current) == 1 and current[0].id == before_id
            # Native Qwen evidence lives inside that same lifecycle-owned volume.
            native = container.exec_run(
                [
                    "node",
                    "-e",
                    (
                        "const fs=require('fs');const p='/workspace/.qwen-home';"
                        "function walk(p){return fs.readdirSync(p,{withFileTypes:true}).flatMap(x=>"
                        "x.isDirectory()?walk(p+'/'+x.name):[p+'/'+x.name]);}"
                        "console.log(walk(p).filter(x=>x.endsWith('.jsonl')).length)"
                    ),
                ]
            )
            assert isinstance(native.output, bytes)
            assert native.exit_code == 0 and int(native.output) >= 1
        finally:
            for agent_id in agents:
                client.post(f"/agents/{agent_id}/stop")
                assert client.delete(f"/agents/{agent_id}").status_code == 204
                assert not (settings.qwen_storage_root / agent_id).exists()
    try:
        for agent_id in agents:
            filters = {"label": f"io.universal-agent-runtime.agent={agent_id}"}
            assert not docker_client.containers.list(all=True, filters=filters)
            assert not docker_client.volumes.list(filters=filters)
    finally:
        docker_client.close()
