# Состояние проекта

Last updated: 2026-09-10 after completing TASK-014.

## Текущий статус

- TASK-000 through TASK-014 are DONE.
- TASK-013 packages the `task-decomposition@1.0.0` Skill, delivers it per Agent, and verifies its restricted confirmed-Task flow with local Docker, Ollama and Qwen Code.
- TASK-014 verifies the full public HTTP, Docker, Qwen Code, external Ollama, Skill, restricted Task Tool, multi-Agent isolation, Session continuity, and cleanup path.
- TASK-004 реализует локальный `DockerRuntime` для runtime-neutral порта `AgentRuntime`.
- TASK-005 добавляет ограниченный исполняемый probe Qwen Code/Ollama, не добавляя conversation/session port в application layer.
- TASK-006 реализует persistent Qwen session adapter за отдельным runtime-neutral портом `AgentInteraction`.
- TASK-007 добавляет universal Docker agent image и проверенный native Qwen launch/resume через `DockerRuntime`.
- TASK-008 добавляет HTTP foundation и явный composition root без endpoints жизненного цикла или чата.
- TASK-009 добавляет Agent lifecycle use cases и HTTP create/inspect/start/stop/delete API без chat behavior.
- TASK-010 implements JSON chat/history. TASK-011 adds turn-bound SSE with committed response content. TASK-012 adds a restricted Task REST MCP adapter.
- JSON chat, SSE event delivery and the restricted Task Tool are implemented. Token streaming and UI are not implemented.

## Работающая функциональность

Проверено по TASK-004:

- `DockerRuntime` реализует `create`, `start`, `status`, `stop` и `delete` с project-owned входами, результатами и стабильными `RuntimeFailure`.
- Логический `workload` разрешается через explicit deployment-owned каталог `DockerWorkload`; Docker image, command, user и container path не попадают в application/domain.
- Каждый Agent получает отдельные container и Docker named volume; backend IDs и SDK objects остаются внутри adapter.
- CPU, memory и PID limits, environment и runtime-resolved secret bindings отображаются в Docker configuration.
- Пустой network tuple для default workload создаёт container с `network_mode=none`; local bridge profile разрешён только когда runtime request точно совпадает с deployment-owned `network_destinations`. Docker bridge не считается destination-filtering enforcement.
- Agent container запускается без privileged mode, со сброшенными capabilities, `no-new-privileges`, read-only root filesystem, ограниченным `tmpfs` и explicit non-root user.
- Host bind mounts и Docker socket не передаются; writable workspace реализован только как managed named volume.
- Readiness подтверждается только для running container с Docker health status `healthy`.
- Идемпотентные retries, timeout/cancellation, partial create/delete failures и recovery handle проверены общим conformance suite.
- Все managed container/volume получают ownership labels; после тестов managed resources отсутствуют.
- Standalone mock Task REST API из TASK-003 продолжает работать без зависимости от runtime package.

Проверено по TASK-005:

- Официальный Qwen Code `0.23.1` запущен из закреплённого container image digest и обращается к внешнему Ollama через настраиваемый OpenAI-compatible `/v1` endpoint.
- Маленькая thinking/tool-capable модель `qwen3:0.6b` (522 MB) загружена в Ollama и использована для ограниченных живых проверок.
- Неинтерактивный `/think` prompt из container вернул точный маркер `QWEN_OLLAMA_PROBE_OK`.
- Безвредный probe предоставил только `read_file`, смонтировал синтетический workspace read-only, наблюдал tool-use event и успешный финальный result event Qwen Code.
- Конфигурация endpoint/model/credential placeholder/timeouts/token/turn/tool budgets явная и валидируется до запуска.
- Structured outcomes различают invalid configuration, Ollama connection/authentication, unavailable model, Qwen process/protocol и scenario expectation failures.
- `--resume` повторно использовал тот же `session_id`, но модель 0.6B не воспроизвела случайный token; conversational persistence не заявляется.

Проверено по TASK-006:

- Runtime-neutral порт `AgentInteraction` отделяет `create_session`, `turn` и `delete_session` от lifecycle-контракта `AgentRuntime`.
- `QwenSessionAdapter` хранит на каждый Agent отдельные manifest, project-owned history, Qwen home, native Qwen chat artifact и workspace.
- Комбинация native Qwen `session_id`/`--resume` и валидируемой project-owned history сохраняет многотуровый контекст после завершения процесса Qwen и повторного открытия adapter.
- Missing, corrupt и incompatible state возвращают стабильные diagnostic failures; adapter не запускает новый несвязанный conversation незаметно.
- Isolation между Agent, отсутствие global mutable Qwen session, явный и идемпотентный cleanup, а также неперсистентность credentials покрыты unit tests.
- Live integration test на `qwen3:1.7b` объективно восстановил случайное codeword во втором ходе после закрытия и повторного открытия adapter, а затем удалил session artifacts.

Проверено по TASK-007:

- `agent_image/` воспроизводимо собирается из маленького committed build context и строго закреплённого официального Qwen Code `0.23.1` image digest.
- Image запускает `/usr/local/bin/agent-runtime` от non-root `10001:10001`, использует `/workspace` как единственный writable per-Agent volume и предоставляет нейтральные launcher-команды `serve`, `readiness`, `turn` и `version`.
- Ollama, model weights, endpoint и credentials не входят в image. Endpoint/model передаются через runtime environment, а `OPENAI_API_KEY` — через `SecretBinding`.
- Launcher сохраняет native Qwen chat state в workspace volume и имеет явные пустые injection slots `/workspace/.agent/skills` и `/workspace/.agent/tools` без преждевременной реализации их формата или Tool behavior.
- Opt-in live test создал и запустил image через `DockerRuntime`, выполнил Qwen turn с внешним Ollama, остановил/запустил тот же runtime, возобновил native session UUID и восстановил случайное codeword.
- Live test проверил read-only root filesystem, dropped capabilities, `no-new-privileges`, non-root user, отсутствие Docker socket, отсутствие Ollama executable/state в image и полное удаление managed container/volume.

Проверено по TASK-008:

- `ApplicationSettings` валидирует обязательную non-secret deployment configuration до создания приложения и допускает только выбранный в composition root `docker` driver.
- `ApplicationComposition` явно владеет runtime и interaction ports на lifespan одного FastAPI app; deterministic fakes передаются напрямую и закрываются при shutdown без hidden process-global state.
- `http_api` использует отдельные Pydantic transport schemas и ограничивает OpenAPI surface маршрутами `GET /healthz` и `GET /readyz`, generated `/openapi.json` и `/docs`.
- Общий redacted error envelope не раскрывает exception details, paths, endpoints, Docker/Qwen output или credentials. На этапе TASK-008 routes для Agent, lifecycle, messages, events и tasks отсутствовали; TASK-009 добавляет только lifecycle surface.
- Configuration, TestClient и OpenAPI tests проходят без Docker, Ollama или корпоративного service.

Проверено по TASK-009:

- `AgentLifecycleService` реализует create, inspect, start/readiness, stop и delete только через project-owned `AgentRuntime`, `AgentInteraction` и `AgentRepository` ports.
- `POST /agents` создаёт уникальные Agent/workspace/Session identities и возвращает `STOPPED`; явные URI запуска и остановки — `POST /agents/{agent_id}/start` и `POST /agents/{agent_id}/stop`.
- Orchestrator владеет переходами `CREATING`, `STARTING`, `READY`, `STOPPING`, `STOPPED`, `FAILED` и не смешивает их с `ExecutionState`/`Readiness` runtime.
- Required `request_id` обеспечивает idempotent create; conflicting payload, concurrent duplicate operations, invalid state, not-found, readiness timeout и partial cleanup имеют детерминированные результаты.
- Per-Agent configuration snapshot содержит только safe workload/resources/capability references; runtime handle, endpoints, secret references/values и backend IDs не сериализуются.
- Stop сохраняет runtime workspace и logical Session. Delete сначала подтверждает runtime cleanup, затем удаляет adapter-owned Session; failure сохраняет record/handle/evidence для retry той же identity.
- `InMemoryAgentRepository` является явной application-lifetime dependency с deletion/idempotency tombstones; hidden process-global state отсутствует.
- Реальный Docker integration test через публичный HTTP API запустил universal agent image до `READY`, выполнил stop/start и подтвердил отсутствие container/volume после delete без обращения к Ollama.

Подробности Qwen/Ollama protocol и ограничений находятся в [docs/qwen-ollama-integration.md](docs/qwen-ollama-integration.md), universal image contract — в [docs/agent-image.md](docs/agent-image.md), HTTP foundation — в [docs/orchestrator-api-foundation.md](docs/orchestrator-api-foundation.md), lifecycle HTTP contract — в [docs/agent-lifecycle-api.md](docs/agent-lifecycle-api.md), Docker mapping — в [docs/docker-runtime.md](docs/docker-runtime.md), а нейтральная семантика — в [docs/runtime-contract.md](docs/runtime-contract.md).

Verified TASK-010 behavior:

- POST/GET `/agents/{agent_id}/messages` expose successful message pairs, opaque IDs, UTC timestamps, sequence ordering, bounded cursor pagination and redacted errors.
- `AgentChatService` uses only project-owned interaction/repository ports; it reserves BUSY before awaiting and rejects overlap. Other Agents remain independent.
- Caller cancellation leaves the owned turn running with BUSY retained; shutdown drains pending turns before closing adapters.
- Recoverable failures preserve runtime and committed history. Native rollback restores the same UUID; a pending marker or indeterminate outcome requires explicit recovery and cannot be bypassed via stop/start.
- `DockerAgentQwenRunner` executes inside the existing lifecycle-owned non-root container and workspace volume. No additional Agent runtime is created by HTTP turns.
- The real Docker/Ollama HTTP test recalls a random codeword after stop/start, verifies native artifacts inside the same container, isolates another Agent's history, and deletes owned resources.

Verified TASK-011 behavior:

- `POST /agents/{agent_id}/messages/stream` delivers typed, versioned SSE events for one accepted turn: started, then committed content/completed or a redacted terminal error.
- Actual Qwen JSONL event types/timing were observed before selecting SSE. The existing adapter buffers its turn result; partial-message mode and token delivery are not claimed.
- JSON and SSE share `AgentChatService.begin`, the same logical Session, BUSY admission, completion, history limits, redaction and failure policy. The public turn ID is allocated at admission and retained in history.
- Keep-alive comments are generated on demand, each network send has a configured timeout, and no event queue or replay cache is introduced.
- ASGI disconnect, network failure and slow-consumer timeout detach delivery without cancelling inference or releasing BUSY. Other Agents continue independently.
- There is no global/Agent-wide subscription or attach-by-ID API. Foreign turn/session request fields are rejected. Last-Event-ID is rejected before admission; clients inspect history after losing a response.
- The real TCP/Uvicorn/Docker/Ollama test received started while a second HTTP request still observed BUSY and empty history, then keep-alive and committed content. A JSON follow-up recalled the SSE turn's random codeword.

Проверено по TASK-012:

- `task_rest_mcp_server.mjs` is an adapter-owned MCP stdio service with exactly
  `get_task`, `create_task`, `create_subtask` and `update_task`. Its routes,
  methods, schemas and response limits are fixed; it accepts no arbitrary URL,
  method, headers or body.
- Deployment configuration supplies the Task API origin, timeout, response-size
  bound and optional secret. The optional token is injected only through runtime
  environment, is absent from generated MCP/Qwen configuration and is redacted
  from adapter artifacts and public chat values.
- Generic Agent `tools` capability IDs are transferred to the isolated runtime.
  `DockerAgentQwenRunner` maps only the recognized Task IDs to Qwen MCP tool
  names. A Skill cannot grant an absent capability.
- Qwen `--bare` receives an explicit adapter-owned `--mcp-config`, one allowed
  MCP server and only selected `task-rest__<operation>` tool names. Built-in
  file, shell, notebook and goal tools remain excluded.
- The deterministic integration test ran MCP discovery and every operation in
  the pinned Qwen image against an isolated HTTP Task fixture. It also covered
  capability denial, input/path/method/header injection, oversized responses,
  not-found, authentication, timeout and service failures.
- An opt-in live Docker/Ollama test using `qwen3:0.6b` confirmed that Qwen Code
  discovered and invoked `create_task` against a real mock Task service. The
  all-operation proof remains deterministic at the MCP protocol boundary.
- Domain/application and the independent `mock_task_service` remain free of
  Task schema imports and decomposition logic.

## Текущая архитектура

`application/ports/agent_runtime.py` остаётся владельцем runtime-neutral контракта. `adapters/docker_runtime.py` зависит от этого порта и инкапсулирует Docker SDK, resource names, labels, status mapping и cleanup. Domain/application не импортируют Docker.

`DockerRuntime` не реализует conversation/session transport и не содержит Qwen-specific behavior. Каталог `DockerWorkload` является composition/deployment input, а не ветвлением use cases. Default workload остаётся без сети; TASK-007 добавляет явный local bridge profile, требующий точного совпадения declared destinations. Он подтверждает local image-to-Ollama connectivity, но намеренно не выдаётся за destination egress enforcement.

`mock_task_service` остаётся отдельным service-plane пакетом и не входит в dependency graph Universal Agent Runtime.

`application/ports/agent_interaction.py` владеет отдельным runtime-neutral conversation contract. `adapters/qwen_session.py` инкапсулирует Qwen CLI, native chat layout, Docker command и persistent adapter artifacts; domain/application видят только общие `AgentId`, `SessionId` и interaction values/failures. `qwen_ollama_probe` остаётся standalone verification package.

`QwenSessionAdapter` остаётся отдельным от lifecycle-операций `DockerRuntime`: TASK-009 координирует их только через независимые ports, создаёт Session после runtime provisioning и удаляет её после runtime cleanup. TASK-010 now rejects concurrent turns and protects BUSY through caller cancellation.

`configuration.py` и `composition.py` выбирают deployment adapter до создания FastAPI app. `http_api.py` зависит только от `ApplicationComposition`, transport schemas и FastAPI, не от Docker/Qwen SDK. FastAPI app хранит composition только в собственном lifespan state; health/readiness относятся к foundation приложения, а не к lifecycle конкретного Agent.

`application/agent_lifecycle.py` координирует Agent state через runtime/session/repository ports. `adapters/in_memory_agent_repository.py` предоставляет explicit process-local metadata state. `http_api.py` преобразует только safe application records/failures в transport schemas; runtime selection и Docker/Qwen calls отсутствуют в handlers.

`application/agent_chat.py` coordinates process-local public history and state. `domain/message.py` contains neutral message values. `adapters/docker_agent_qwen.py` encapsulates ownership-label discovery, Qwen execution and bounded native-state transfer into the existing Agent runtime. API history is separate from the persistent adapter context. [Chat contract](docs/agent-chat-api.md) and ADR-0006 define the relationship.

`http_streaming.py` owns transport-only SSE schemas, framing and bounded sends.
`AgentChatService.begin` is shared admission for JSON and SSE. Lifecycle and
interaction ports, Qwen adapters and runtime drivers are unchanged by TASK-011.
[Streaming contract](docs/agent-streaming-api.md) and ADR-0007 explicitly distinguish
SSE event delivery from incremental token delivery.

## Предположения об окружении

- Workspace: `C:\Users\Kirill\Desktop\agentt_serv`, Windows PowerShell 5.1.
- Git root: `C:\Users\Kirill\Desktop\agentt_serv`, branch `main`, remote-tracking branch `origin/main`.
- Проверки выполнены с Python 3.11.9, pytest 8.4.2, Ruff 0.16.6 и mypy 1.20.2.
- Docker Desktop context `desktop-linux` и Docker Engine 29.2.1 были доступны для реальных integration tests.
- Закреплены Docker SDK 7.2.0 и dev stubs `types-docker` 7.2.0.20260827; ранее закреплены FastAPI 0.141.1, Uvicorn 0.52.4 и HTTPX 0.28.1.
- Purpose-built test image собирается из `tests/docker_assets/Dockerfile` на закреплённом digest BusyBox; это не будущий Qwen image.
- Qwen Code `0.23.1` проверен в официальном image digest `sha256:996a12729e25f694254768ac8d3b5f870c54e6ac5825e169299e85a19c78cc10`.
- Внешний локальный Ollama `0.24.0` и модель `qwen3:0.6b` с local list ID `7df6b6e09427` были доступны для живых проверок.
- Для воспроизводимой live session-проверки TASK-006 использована `qwen3:1.7b` (1.4 GB), local list ID `8f68893c685c`, weights blob `sha256:3d0b790534fe4b79525fc3692950408dca41171676ed7e21db57af5c65ef6ab6`.
- Default Docker bridge разрешил `host.docker.internal` до host Ollama из universal agent image, запущенного `DockerRuntime`; это проверенный local integration path, но не destination egress enforcement.
- Корпоративные Qwen/Ollama endpoints, authentication, TLS/proxy/certificate trust и сервисы остаются `NOT VERIFIED`.

- TASK-010 live HTTP integration used the pinned universal image and configured local `qwen3:1.7b`; the second request recalled the first request's random codeword.

- TASK-011 used Uvicorn/HTTPX over a real ephemeral loopback TCP socket, the pinned Agent image, and local external `qwen3:1.7b`. Early SSE event delivery and SSE-to-JSON context reuse passed.

## Известные ограничения

- Автоматическое восстановление in-memory records/tombstones новым процессом не реализовано. Docker resources имеют полные ownership labels и обнаружимы; durable metadata store относится к последующей persistence-задаче и остаётся `NOT VERIFIED`.
- `DockerWorkload` может объявить bridge profile для exact local integration destinations, но Docker bridge не фильтрует фактический egress. Production network enforcement остаётся `NOT VERIFIED` до отдельной policy/Kata-задачи.
- Docker health status является только backend readiness signal; Qwen interaction readiness относится к последующим задачам.
- Mock Task store остаётся single-process и in-memory; corporate schema/auth/persistence не реализованы.
- `qwen3:0.6b` успешно выполняет bounded prompt и выдаёт `read_file` tool call, но неточно следует требованию к финальному тексту после tool result.
- Native-only Qwen Code `--resume` на 0.6B-модели был семантически нестабилен. TASK-006 решает это комбинацией native artifact и project-owned history; live проверка на 1.7B прошла.
- Автоматическое восстановление Agent/Session после будущего перезапуска Orchestrator не реализовано; adapter artifacts и ожидаемые recovery checks задокументированы.
- Conversation and public history have explicit configured bounds. TASK-010 rejects excess history without truncation and rejects concurrent turns. Compression/summarization are not implemented.
- `qwen3:4b-thinking` на CPU достиг 360-second wall-time budget с exit status 55; поэтому воспроизводимый default probe использует 0.6B.
- Durable Orchestrator metadata и восстановление Agent records/tombstones после restart не реализованы; process-local `InMemoryAgentRepository` теряет их при остановке приложения. Это остаётся `NOT VERIFIED` для последующей persistence/recovery работы.
- Shutdown приложения не выполняет автоматический delete существующих Agents; оператор обязан остановить/удалить их через API. Crash recovery runtime resources остаётся `NOT VERIFIED` без durable metadata.
- TASK-010 implements message/history endpoints and BUSY transitions; TASK-011 adds turn-bound SSE. Response content is buffered until commit: this is not token streaming.
- Реальные Kata и корпоративные Qwen/Ollama integrations остаются `NOT VERIFIED`.
- Pre-existing working-tree changes from earlier tasks and user model recommendations were preserved; only TASK-011 was advanced to DONE during this task.

- Public message IDs/timestamps and recovery flags are process-local. Automatic restart recovery is NOT VERIFIED because no durable Agent repository is implemented.
- POST messages has no idempotency key. After a lost HTTP response, inspect history before resubmission; accepted turns continue after caller cancellation.
- Tools remain disabled. Rollback of future external Tool writes is NOT VERIFIED and not claimed.
- Known injected credential values are redacted from message content; arbitrary user-supplied secrets are not automatically detected.

- Incremental token/partial-message delivery is NOT VERIFIED and not implemented; the SSE content event contains one full committed response.
- SSE replay and automatic reconnection are not supported. On disconnect the accepted turn continues; a terminal frame may be lost. Durable event replay and remote proxy buffering remain NOT VERIFIED.
- Streaming uses the same local API access assumptions as JSON chat. No authentication or tenant authorization infrastructure was added.

## Решения

ADR-0001 through ADR-0005 remain in force. ADR-0006 defines public chat commit and recovery ownership. ADR-0007 selects turn-bound SSE with committed response content. ADR-0005 фиксирует комбинацию native Qwen session artifact и project-owned validated history как durable persistence mechanism TASK-006.

- [ADR-0001](docs/decisions/0001-runtime-port-and-driver-boundary.md)
- [ADR-0002](docs/decisions/0002-per-agent-isolation-boundary.md)
- [ADR-0003](docs/decisions/0003-external-inference-boundary.md)
- [ADR-0004](docs/decisions/0004-runtime-retry-and-recovery-ownership.md)
- [ADR-0005](docs/decisions/0005-qwen-session-persistence.md)
- [ADR-0006](docs/decisions/0006-public-chat-commit-and-recovery.md)
- [ADR-0007](docs/decisions/0007-turn-bound-committed-sse.md)

## Важные команды

Запускать из `C:\Users\Kirill\Desktop\agentt_serv`:

```powershell
# Установка пакета и закреплённых dependencies
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# Общий AgentRuntime contract на fake и реальном Docker driver
.\.venv\Scripts\python.exe -m pytest tests/contract -q

# Docker-specific integration/security mapping
.\.venv\Scripts\python.exe -m pytest tests/integration/test_docker_runtime.py -q

# Ограниченные живые проверки Qwen Code/Ollama
ollama pull qwen3:0.6b
.\.venv\Scripts\python.exe -m qwen_ollama_probe prompt
.\.venv\Scripts\python.exe -m qwen_ollama_probe tool
.\.venv\Scripts\python.exe -m qwen_ollama_probe session

# Unit и opt-in live persistence tests TASK-006
.\.venv\Scripts\python.exe -m pytest tests/unit/test_qwen_session.py -q
$env:RUN_QWEN_OLLAMA_INTEGRATION='1'
$env:QWEN_OLLAMA_MODEL='qwen3:1.7b'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_qwen_session.py -q

# Universal agent image: build и opt-in live Qwen turn/resume через DockerRuntime
docker build --pull=false --tag uar-task007-agent:local agent_image
.\.venv\Scripts\python.exe -m pytest tests/integration/test_agent_image.py -q

# HTTP foundation, TestClient и generated OpenAPI без external services
.\.venv\Scripts\python.exe -m pytest tests/api/test_orchestrator_foundation.py -q

# Agent lifecycle unit/API tests без external services
.\.venv\Scripts\python.exe -m pytest tests/unit/test_agent_lifecycle.py tests/api/test_agent_lifecycle_api.py -q

# Public lifecycle API с реальным DockerRuntime и universal agent image
.\.venv\Scripts\python.exe -m pytest tests/integration/test_agent_lifecycle_api.py -q

# TASK-010 deterministic chat/transport tests and live HTTP continuity
.\.venv\Scripts\python.exe -m pytest tests/unit/test_agent_chat.py tests/unit/test_docker_agent_qwen.py tests/unit/test_qwen_session.py tests/api/test_agent_chat_api.py -q
$env:RUN_QWEN_OLLAMA_INTEGRATION='1'
$env:QWEN_OLLAMA_MODEL='qwen3:1.7b'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_agent_chat_api.py -q

# TASK-011 deterministic SSE and live TCP/Docker/Ollama validation
.\.venv\Scripts\python.exe -m pytest tests/api/test_agent_streaming.py tests/unit/test_agent_chat.py tests/api/test_agent_chat_api.py -q
$env:RUN_QWEN_OLLAMA_INTEGRATION='1'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_agent_streaming.py -q

# TASK-012 restricted Task MCP protocol and live Qwen discovery/invocation
.\.venv\Scripts\python.exe -m pytest tests/unit/test_task_rest_mcp.py tests/unit/test_qwen_session.py tests/unit/test_docker_agent_qwen.py -q
$env:RUN_QWEN_OLLAMA_INTEGRATION='1'
$env:QWEN_OLLAMA_MODEL='qwen3:0.6b'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_task_rest_tool.py -q

# Полный quality gate
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy src tests

# Проверка cleanup, статусов и scoped diff
docker ps -a --filter label=io.universal-agent-runtime.managed=true
docker volume ls --filter label=io.universal-agent-runtime.managed=true
rg -n '^Статус: (TODO|ACTIVE|DONE|BLOCKED)$' TASKS.md
git status --short -- .
git diff --check -- .
```

## TASK-012 validation archive

Date: 2026-09-09. Result: PASS for TASK-012.

- Focused Task MCP/Qwen/lifecycle/configuration checks: 81 passed, 1 opt-in
  live test skipped in the default run.
- Deterministic MCP protocol integration ran in the pinned Qwen Code image:
  all four allowed operations passed against an isolated Task HTTP fixture.
- Separately enabled live Docker/Ollama check: 1 passed in 16.10 s using
  `qwen3:0.6b`; Qwen Code discovered and invoked the restricted `create_task`
  MCP tool against a real mock Task service.
- Full pytest: 334 passed, 5 opt-in live tests skipped.
- Ruff format --check: PASS, 95 files already formatted. Ruff lint: PASS.
- mypy src tests: PASS, 70 source files.
- Managed Docker container and volume inventories: empty after validation.
- Scoped diff compared against the pre-task snapshot: Task MCP adapter,
  deployment configuration, capability plumbing, tests and documentation only;
  existing unrelated working-tree changes were preserved.
- git diff --check: PASS. No dependencies or unrelated infrastructure added.
- Existing Starlette/HTTPX/AnyIO deprecation warnings remain; no test failures.
- All eight TASK-012 acceptance criteria were verified. Evidence mapping:
  [restricted-task-rest-tool.md](docs/restricted-task-rest-tool.md).

## Архив валидации TASK-011

Date: 2026-09-09. Result: PASS for TASK-011.

- Initial focused SSE/chat/lifecycle checks: 42 passed; the additional ASGI
  disconnect test subsequently passed in the full suite.
- Full pytest: 315 passed, 4 opt-in live tests skipped in that run.
- Separately enabled live TCP SSE test: 1 passed in 24.57 s. It verifies early
  started delivery while BUSY, keep-alive, one committed content frame,
  terminal completion, public history IDs, and context reuse by JSON chat.
- Separately observed current Qwen output through the existing TASK-010 live
  test: 1 passed in 35.64 s. Safe event-type timings are recorded in the streaming
  contract; no raw prompts/output/credentials were retained in the evidence log.
- Ruff format --check: PASS, 91 files already formatted. Ruff lint: PASS.
- mypy src tests: PASS, 68 source files.
- Managed Docker container and volume inventories: empty after validation.
- Scoped diff compared against a pre-task workspace snapshot: only TASK-011
  code/tests/docs/configuration changed. Existing unrelated changes preserved.
- git diff --check: PASS. No new dependencies, tool operations, or infrastructure.
- Existing Starlette/HTTPX/AnyIO deprecation warnings remain; no test failures.
- All eight acceptance criteria verified with the explicit buffered-content
  limitation. Evidence mapping: [agent-streaming-api.md](docs/agent-streaming-api.md).

## TASK-012 changed files

- `.env.example`, `AGENTS.md`, `TASKS.md`, `PROJECT_STATE.md`, `pyproject.toml`
- `docs/architecture.md`, `docs/restricted-task-rest-tool.md`,
  `docs/decisions/0008-restricted-task-mcp-boundary.md`
- `src/universal_agent_runtime/configuration.py`, `composition.py`,
  `application/agent_lifecycle.py`
- `src/universal_agent_runtime/adapters/qwen_session.py`,
  `docker_agent_qwen.py`, `task_rest_mcp_server.mjs`
- `tests/api/test_orchestrator_foundation.py`,
  `tests/unit/test_agent_lifecycle.py`, `test_qwen_session.py`,
  `test_task_rest_mcp.py`, `tests/integration/test_task_rest_tool.py`

## TASK-011 changed files

- `src/universal_agent_runtime/application/agent_chat.py`
- `src/universal_agent_runtime/http_streaming.py`
- `src/universal_agent_runtime/http_api.py`
- `src/universal_agent_runtime/configuration.py`
- `tests/api/test_agent_streaming.py`
- `tests/api/test_agent_lifecycle_api.py`
- `tests/integration/test_agent_streaming.py`
- `.env.example`, `AGENTS.md`, `TASKS.md`, `PROJECT_STATE.md`
- `docs/agent-streaming-api.md`, `docs/decisions/0007-turn-bound-committed-sse.md`
- `docs/architecture.md`, `docs/agent-chat-api.md`, `docs/agent-lifecycle-api.md`,
  `docs/orchestrator-api-foundation.md`

## TASK-010 changed files

The following list describes this task, excluding pre-existing working-tree
changes that were preserved without modification:

- `.env.example`, `AGENTS.md`, `PROJECT_STATE.md`, `TASKS.md`
- `docs/architecture.md`, `docs/agent-chat-api.md`, `docs/agent-lifecycle-api.md`,
  `docs/orchestrator-api-foundation.md`, `docs/qwen-session.md`
- `docs/decisions/0005-qwen-session-persistence.md`,
  `docs/decisions/0006-public-chat-commit-and-recovery.md`
- `src/universal_agent_runtime/application/agent_chat.py`,
  `src/universal_agent_runtime/application/agent_lifecycle.py`
- `src/universal_agent_runtime/application/ports/agent_interaction.py`,
  `src/universal_agent_runtime/application/ports/interaction_errors.py`,
  `src/universal_agent_runtime/application/ports/interaction_values.py`
- `src/universal_agent_runtime/domain/agent.py`,
  `src/universal_agent_runtime/domain/message.py`
- `src/universal_agent_runtime/adapters/docker_agent_qwen.py`,
  `src/universal_agent_runtime/adapters/qwen_session.py`
- `src/universal_agent_runtime/composition.py`,
  `src/universal_agent_runtime/configuration.py`,
  `src/universal_agent_runtime/http_api.py`
- `tests/unit/test_agent_chat.py`, `tests/unit/test_docker_agent_qwen.py`,
  `tests/unit/test_qwen_session.py`, `tests/unit/test_dependency_direction.py`
- `tests/api/test_agent_chat_api.py`, `tests/api/test_agent_lifecycle_api.py`,
  `tests/api/test_orchestrator_foundation.py`
- `tests/integration/test_agent_chat_api.py`,
  `tests/runtime_support/fake_interaction.py`

## TASK-013 validation

- Package manifest parsing, capability intersection, workspace delivery and
  prompt composition: PASS through focused unit tests.
- Full pytest: 341 passed, 5 opt-in live tests skipped; two existing TestClient
  deprecation warnings only.
- Ruff lint and mypy `src tests`: PASS. `git diff --check`: PASS.
- Opt-in `tests/integration/test_task_rest_tool.py` with Docker, local Ollama,
  `qwen3:0.6b`, selected `task-decomposition`, and restricted MCP: PASS. The
  explicit confirmed proposal created only the allowed Task record.
- Scoped diff is limited to Skill packaging, generic package capability
  transport, Qwen adapter delivery, its tests, and documentation. Earlier
  TASK-012 working-tree changes remain preserved.

## TASK-014 latest validation

Date: 2026-09-10. Result: PASS for TASK-014.

- Opt-in full local E2E: 1 passed in 291.35 s with real Docker Desktop,
  external Ollama, `qwen3:1.7b`, Qwen Code `0.23.1`, and `/no_think`.
- The test started real TCP Orchestrator and mock Task services, created and
  awaited two READY Agents only through public Orchestrator APIs, and observed
  two distinct managed containers and workspaces.
- JSON proposal and committed-content SSE revision completed. Public Task API
  reads proved no mutation before explicit confirmation.
- Confirmed `create_task` and `create_subtask` calls produced exact validated
  records in the public responses; the Task API verified `task-0001` as parent
  of `task-0002`. Per-turn duplicate mutation calls are idempotent.
- Stop/start preserved the first Agent's unpredictable marker. A subsequent
  turn in the second Agent did not contain it, proving runtime, Workspace, and
  Session isolation.
- Full pytest: 351 passed, 6 opt-in tests skipped; two existing
  Starlette/HTTPX/AnyIO deprecation warnings only.
- Focused post-type-fix session/transport tests: 53 passed. Earlier focused
  Task/Qwen/configuration checks: 88 passed.
- Ruff format --check: PASS, 104 files already formatted. Ruff lint: PASS.
- mypy `src tests`: PASS, 75 source files. `git diff --check`: PASS.
- Managed Docker container and volume inventories were empty after the passing
  scenario and final validation.
- All TASK-014 acceptance criteria are verified. Evidence and retry procedure:
  [local-docker-e2e.md](docs/local-docker-e2e.md).

## TASK-014 changed files

- `.env.example`, `AGENTS.md`, `TASKS.md`, `PROJECT_STATE.md`
- `docs/architecture.md`, `docs/local-docker-e2e.md`,
  `docs/orchestrator-api-foundation.md`, `docs/qwen-session.md`,
  `docs/skill-packaging.md`
- `src/universal_agent_runtime/configuration.py`, `composition.py`
- `src/universal_agent_runtime/adapters/docker_agent_qwen.py`,
  `qwen_session.py`, `skill_packages.py`, `task_rest_mcp_server.mjs`
- `src/universal_agent_runtime/agent_assets/task-decomposition/SKILL.md`,
  `skill.json`
- `tests/e2e/test_local_docker_task_decomposition.py`
- `tests/api/test_orchestrator_foundation.py`
- `tests/unit/test_docker_agent_qwen.py`, `test_qwen_session.py`,
  `test_skill_packages.py`, `test_task_rest_mcp.py`

## Recommended next task

TASK-015 - Kata environment research and validation.

TASK-015 remains TODO. Do not start without an explicit user instruction.

Recommended model: GPT-6 Astra.

Recommended reasoning level: High.
