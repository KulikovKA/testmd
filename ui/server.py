from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import FastAPI, Path as ApiPath, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

ORCHESTRATOR_BASE_URL = os.getenv(
    "UAR_UI_ORCHESTRATOR_BASE_URL", "http://127.0.0.1:8080"
).rstrip("/")
UPSTREAM_TIMEOUT_SECONDS = float(os.getenv("UAR_UI_UPSTREAM_TIMEOUT_SECONDS", "420"))
UI_HOST = os.getenv("UAR_UI_HOST", "127.0.0.1")
UI_PORT = int(os.getenv("UAR_UI_PORT", "8090"))
INDEX_FILE = Path(__file__).with_name("index.html")


class CreateAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
    )
    skills: list[str] = Field(default_factory=list, max_length=32)
    tools: list[str] = Field(default_factory=list, max_length=32)


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=16384)


def _decode_response(response: Any) -> Any:
    raw = response.read()
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "error": {
                "code": "invalid_upstream_response",
                "message": "Orchestrator returned an invalid response",
            }
        }


def _request_sync(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(
        ORCHESTRATOR_BASE_URL + path,
        method=method,
        headers=headers,
        data=data,
    )

    try:
        with urlopen(req, timeout=UPSTREAM_TIMEOUT_SECONDS) as response:
            return response.status, _decode_response(response)
    except HTTPError as exc:
        return exc.code, _decode_response(exc)
    except (URLError, TimeoutError, OSError):
        return 502, {
            "error": {
                "code": "orchestrator_unavailable",
                "message": "Orchestrator is unavailable",
            }
        }


async def _request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    return await asyncio.to_thread(_request_sync, method, path, payload)


def _response(status_code: int, payload: Any) -> Response:
    if status_code == 204:
        return Response(status_code=204)
    return JSONResponse(status_code=status_code, content=payload)


app = FastAPI(
    title="Universal Agent Runtime UI",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/", include_in_schema=False)
@app.get("/ui", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(INDEX_FILE, media_type="text/html; charset=utf-8")


@app.get("/api/status")
async def status() -> Response:
    health_status, health = await _request("GET", "/healthz")
    if health_status >= 400:
        return _response(health_status, health)

    ready_status, ready = await _request("GET", "/readyz")
    if ready_status >= 400:
        return _response(ready_status, ready)

    return JSONResponse(
        {
            "health": health,
            "readiness": ready,
            "orchestrator_base_url": ORCHESTRATOR_BASE_URL,
        }
    )


@app.post("/api/agents")
async def create_agent(body: CreateAgentRequest) -> Response:
    status_code, payload = await _request(
        "POST", "/agents", body.model_dump(mode="json")
    )
    return _response(status_code, payload)


@app.get("/api/agents/{agent_id}")
async def inspect_agent(
    agent_id: str = ApiPath(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$", max_length=64),
) -> Response:
    status_code, payload = await _request("GET", f"/agents/{quote(agent_id)}")
    return _response(status_code, payload)


@app.post("/api/agents/{agent_id}/start")
async def start_agent(
    agent_id: str = ApiPath(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$", max_length=64),
) -> Response:
    status_code, payload = await _request(
        "POST", f"/agents/{quote(agent_id)}/start"
    )
    return _response(status_code, payload)


@app.post("/api/agents/{agent_id}/stop")
async def stop_agent(
    agent_id: str = ApiPath(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$", max_length=64),
) -> Response:
    status_code, payload = await _request(
        "POST", f"/agents/{quote(agent_id)}/stop"
    )
    return _response(status_code, payload)


@app.delete("/api/agents/{agent_id}")
async def delete_agent(
    agent_id: str = ApiPath(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$", max_length=64),
) -> Response:
    status_code, payload = await _request("DELETE", f"/agents/{quote(agent_id)}")
    return _response(status_code, payload)


@app.get("/api/agents/{agent_id}/messages")
async def history(
    agent_id: str = ApiPath(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$", max_length=64),
    after: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=50),
) -> Response:
    status_code, payload = await _request(
        "GET",
        f"/agents/{quote(agent_id)}/messages?after={after}&limit={limit}",
    )
    return _response(status_code, payload)


@app.post("/api/agents/{agent_id}/messages")
async def send_message(
    body: MessageRequest,
    agent_id: str = ApiPath(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$", max_length=64),
) -> Response:
    status_code, payload = await _request(
        "POST",
        f"/agents/{quote(agent_id)}/messages",
        body.model_dump(mode="json"),
    )
    return _response(status_code, payload)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=UI_HOST, port=UI_PORT)
