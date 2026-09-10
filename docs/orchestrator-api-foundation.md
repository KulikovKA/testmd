# Основа API Agent Orchestrator

## Scope

TASK-008 создаёт только HTTP foundation. `create_application_from_environment()`
валидирует configuration, выбирает `DockerRuntime` в composition root и
связывает его с отдельным `QwenSessionAdapter` через runtime-neutral ports.
`create_application(composition)` принимает явный `ApplicationComposition`,
поэтому unit/API tests могут передавать deterministic fakes без Docker, Ollama
или process-global state.

TASK-009 extends this foundation with the Agent lifecycle use cases and routes
documented in [agent-lifecycle-api.md](agent-lifecycle-api.md). TASK-010 now
adds [JSON chat/history](agent-chat-api.md); TASK-011 adds
[turn-bound SSE](agent-streaming-api.md). Task-specific endpoints remain absent.

## Configuration

`ApplicationSettings.from_environment()` проверяет обязательные значения до
создания FastAPI app. Безопасный local example находится в `.env.example`; файл
`.env` не загружается автоматически.

| Переменная | Назначение |
| --- | --- |
| `UAR_API_HOST`, `UAR_API_PORT` | Bind host и port процесса Uvicorn |
| `UAR_RUNTIME_DRIVER` | Сейчас разрешено только `docker`; Kata не выбирается раньше своей задачи |
| `UAR_DOCKER_WORKLOAD_*` | Image, command JSON-array, user и workspace target для deployment-owned `DockerWorkload` |
| `UAR_DOCKER_NETWORK_*` | Явный `none` или проверенный local `bridge` profile TASK-007 |
| `UAR_DOCKER_HEALTHCHECK_*` | Universal image readiness command timing and retry policy |
| `UAR_AGENT_CPU_CORES`, `UAR_AGENT_MEMORY_BYTES` | Per-Agent runtime resource snapshot |
| `UAR_AGENT_OPERATION_TIMEOUT_SECONDS` | Bound for one runtime operation |
| `UAR_AGENT_READINESS_TIMEOUT_SECONDS`, `UAR_AGENT_READINESS_POLL_INTERVAL_SECONDS` | Overall start/readiness polling policy |
| `UAR_QWEN_SESSION_STORAGE_ROOT` | Не-root local storage для adapter-owned Session artifacts |
| `UAR_QWEN_BASE_URL`, `UAR_QWEN_MODEL` | Deployment-owned внешний Ollama endpoint и model |
| `UAR_QWEN_REASONING_DIRECTIVE` | Validated `/think` or `/no_think`; defaults to `/think` |
| `UAR_QWEN_API_KEY_SECRET_ID` | Non-secret runtime reference used by `SecretBinding` |
| `UAR_QWEN_API_KEY` | Runtime credential; safe `ollama` placeholder допустим только для локального Ollama |

Значение `UAR_QWEN_API_KEY` не включается в `repr` configuration, transport
responses или общий error envelope. TASK-009 resolves its configured non-secret
reference through `SecretBinding` only inside the selected runtime adapter.

## HTTP contract

| Method и path | Назначение | Успешный ответ |
| --- | --- | --- |
| `GET /healthz` | Liveness API process | `{"status":"ok"}` |
| `GET /readyz` | Foundation readiness после валидированной composition | `{"status":"ready","runtime_driver":"docker"}` |
| `GET /openapi.json` | Машиночитаемая документация | OpenAPI document with foundation and TASK-009 lifecycle routes |
| `GET /docs` | Swagger UI | Generated API documentation |

`/readyz` не создаёт Agent, не запускает Docker container и не проверяет
доступность Ollama. Он означает только то, что application composition уже
создана с валидированными configuration и dependency lifetimes. Readiness
конкретного Agent проверяется только через explicit TASK-009 start operation.

Все HTTP errors используют redacted envelope:

```json
{
  "error": {
    "code": "not_found",
    "message": "Resource not found"
  }
}
```

Envelope не возвращает exception details, paths, endpoint values, credentials,
Docker/Qwen output или внутренние object representations.

## Dependency lifetime

`ApplicationComposition` владеет runtime и interaction adapters ровно в рамках
lifespan одного FastAPI app. Они передаются явно, хранятся только в
`app.state.composition` и закрываются при shutdown. HTTP handlers не выбирают
runtime driver и не создают adapters; выбор `docker` происходит в
`compose_application()`.

## Reproducible commands

```powershell
# Set the safe local values from .env.example in the current shell, then start
# only the foundation app. This does not create an Agent or contact Ollama.
$env:UAR_API_HOST='127.0.0.1'
$env:UAR_API_PORT='8080'
$env:UAR_RUNTIME_DRIVER='docker'
$env:UAR_DOCKER_WORKLOAD_KEY='qwen-agent-image'
$env:UAR_DOCKER_WORKLOAD_IMAGE='uar-task007-agent:local'
$env:UAR_DOCKER_WORKLOAD_COMMAND_JSON='["serve"]'
$env:UAR_DOCKER_WORKLOAD_USER='10001:10001'
$env:UAR_DOCKER_WORKSPACE_TARGET='/workspace'
$env:UAR_DOCKER_NETWORK_MODE='bridge'
$env:UAR_DOCKER_NETWORK_HOST='host.docker.internal'
$env:UAR_DOCKER_NETWORK_PORT='11434'
$env:UAR_DOCKER_HEALTHCHECK_INTERVAL_SECONDS='1'
$env:UAR_DOCKER_HEALTHCHECK_TIMEOUT_SECONDS='2'
$env:UAR_DOCKER_HEALTHCHECK_RETRIES='30'
$env:UAR_DOCKER_HEALTHCHECK_START_PERIOD_SECONDS='1'
$env:UAR_AGENT_CPU_CORES='1.0'
$env:UAR_AGENT_MEMORY_BYTES='1073741824'
$env:UAR_AGENT_OPERATION_TIMEOUT_SECONDS='30'
$env:UAR_AGENT_READINESS_TIMEOUT_SECONDS='30'
$env:UAR_AGENT_READINESS_POLL_INTERVAL_SECONDS='0.1'
$env:UAR_QWEN_SESSION_STORAGE_ROOT='.runtime/qwen-sessions'
$env:UAR_QWEN_BASE_URL='http://host.docker.internal:11434/v1'
$env:UAR_QWEN_MODEL='qwen3:1.7b'
$env:UAR_QWEN_REASONING_DIRECTIVE='/think'
$env:UAR_QWEN_API_KEY_SECRET_ID='local-ollama-key'
$env:UAR_QWEN_API_KEY='ollama'
.\.venv\Scripts\python.exe -m uvicorn universal_agent_runtime.http_api:create_application_from_environment --factory --host $env:UAR_API_HOST --port $env:UAR_API_PORT

# In another shell.
Invoke-RestMethod http://127.0.0.1:8080/healthz
Invoke-RestMethod http://127.0.0.1:8080/readyz
Invoke-WebRequest http://127.0.0.1:8080/openapi.json -OutFile openapi.json
$agent = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/agents -ContentType application/json -Body '{"request_id":"manual-check"}'
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8080/agents/$($agent.agent_id)/start"
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8080/agents/$($agent.agent_id)/stop"
Invoke-RestMethod -Method Delete -Uri "http://127.0.0.1:8080/agents/$($agent.agent_id)"

# Deterministic TestClient and generated OpenAPI checks; no Docker/Ollama needed.
.\.venv\Scripts\python.exe -m pytest tests/api/test_orchestrator_foundation.py -q
```

TASK-010 composition also owns `AgentChatService`, drains accepted turns at shutdown,
and selects `DockerAgentQwenRunner` for execution inside each lifecycle-owned Agent.
