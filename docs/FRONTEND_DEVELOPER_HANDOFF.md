# Universal Agent Runtime — Frontend Developer Handoff

## 1. Назначение и границы системы

Universal Agent Runtime (UAR) — FastAPI backend и runtime для изолированных AI-Agent. Frontend создаёт Agent, управляет его lifecycle и запускает задачи; UAR принимает deterministic решения о состоянии, sandbox, доступах, файловых операциях и Git. Концептуальная inference-цепочка текущего R&D deployment:

```text
User / Frontend -> UAR Orchestrator -> Kata Agent -> Qwen Code
                                                -> LiteLLM -> vLLM -> qwen3.8-27b

DevelopmentTask: Agent workspace -> local Git commit -> Trusted Git Helper -> Sfera Git
```

**Model != Agent != Harness.** Model (`qwen3.8-27b`) — вероятностный inference core. Agent — отдельная изолированная runtime-среда с workspace, Qwen Code и Skills. Harness/UAR — deterministic control plane: lifecycle, policy, sandbox, state machine, redaction, observability и Git boundary. LLM предлагает reasoning/план/полное содержимое файлов; он не владеет credentials, не выбирает target branch и не выполняет произвольный Git push.

Реализовано в: `src/universal_agent_runtime/http_api.py`, `composition.py`, `application/development_workflow.py`.

## 2. Архитектура верхнего уровня

```text
Frontend / Postman
       | HTTP, AG-UI SSE
       v
FastAPI API ----------------------------- GET task/trace/agent
       |
       v
UAR Orchestrator (ApplicationComposition)
       |                         |
       | lifecycle/control plane | trusted, credential-bearing only
       v                         v
Kata/Docker managed Agent   disposable Git Helper ---- SSH ---- Sfera Git
       |                         ^
       | /workspace volume       | key + known_hosts: read-only helper mounts
       +-- Qwen Code              |
       |     |                    |
       |     +-- LiteLLM -> vLLM -> qwen3.8-27b
       +-- Skills, project checkout, credential-free local Git
```

`ApplicationComposition` собирает production adapters и application services. `DockerRuntime` управляет container runtime через Docker SDK; при `runtime_driver=kata` выбирается Docker runtime `kata`. Docker API здесь control plane, а не доказательство обычного runc execution.

Реализовано в: `composition.py`, `adapters/docker_runtime.py`, `adapters/docker_trusted_git.py`.

## 3. Ключевые backend-компоненты

| Компонент | Файл / основной класс | Назначение и что важно UI |
| --- | --- | --- |
| Composition | `composition.py`, `ApplicationComposition` | Связывает settings, lifecycle, chat, development, runtime. Реестры Agent/task process-local. |
| Lifecycle | `application/agent_lifecycle.py`, `AgentLifecycleService` | Create/start/stop/delete, runtime/session/skills. UI показывает `state`, `failure`, `runtime`. |
| Chat | `application/agent_chat.py`, `AgentChatService` | Обычные сообщения и owned turns; не путать с development workflow. |
| Tasks | `application/development_tasks.py`, `DevelopmentTaskService` | Создаёт task, claim/release Agent, хранит state/version/trace. |
| Workflow | `application/development_workflow.py`, `DevelopmentWorkflow` | requirements, plan, workspace, implementation, commit/push; текущий режим code-only. |
| Runtime | `adapters/docker_runtime.py`, `DockerRuntime` | Создаёт managed Agent volume/container, читает workspace inventory. |
| Qwen runner | `adapters/docker_agent_qwen.py`, `DockerAgentQwenRunner` | Запускает Qwen Code через Docker `exec` в lifecycle-owned Agent. |
| Qwen session | `adapters/qwen_session.py`, `QwenSessionAdapter` | Host-side session state/history, sync Qwen home и transcript projection. |
| Workspace | `adapters/docker_development_workspace.py`, `DockerDevelopmentWorkspaceAdapter` | Передаёт закрытые `WorkspaceRequest` в `workspace-operations.mjs`. |
| Trusted Git | `adapters/docker_trusted_git.py`, `DockerTrustedGitAdapter` | Clone/push в disposable helper; Agent не получает SSH key. |
| Skills | `adapters/skill_packages.py`, `SkillPackageCatalog` | Built-in/registry package selection and materialization. |

## 4. Agent и lifecycle

Agent — не HTTP request и не LLM session. Record содержит `agent_id`, `workspace_id`, session, configuration (workload/resources/skills/tools), runtime handle/observation, state, failure и recovery flag. `POST /agents` создаёт record/session, но Agent ещё не `READY`: нужен отдельный `POST /agents/{agent_id}/start`.

Фактические public states: `CREATING`, `STARTING`, `READY`, `BUSY`, `STOPPING`, `STOPPED`, `FAILED`. Удаление не является state: `DELETE /agents/{agent_id}` удаляет record и отвечает `204`.

```text
POST /agents -> STOPPED -> start -> STARTING -> READY <-> BUSY
                                      |                 |
                                      +---------------> FAILED
READY/FAILED/STOPPED -> stop -> STOPPING -> STOPPED -> delete (204)
```

UI: после create сохранить `agent_id`; poll `GET /agents/{agent_id}` до `READY`. Start/stop/delete допускаются только в разрешённых lifecycle состояниях; conflict приходит как HTTP `409` с safe error envelope. Не предполагайте idempotency кроме create: один `request_id` используется lifecycle service как idempotency identity и повтор create возвращает existing Agent с HTTP 200.

Реализовано в: `domain/agent.py`, `application/agent_lifecycle.py`, `http_api.py`.

## 5. Kata, sandbox и workspace

Один Agent соответствует одному managed runtime и отдельному Docker volume. При `kata` workload создаётся с runtime `kata`; Agent A и Agent B имеют разные sandbox и workspace.

```text
Agent A -> Kata sandbox A -> volume A -> /workspace
Agent B -> Kata sandbox B -> volume B -> /workspace
```

`DockerRuntime` создаёт volume, монтирует его в configured absolute workspace target (production ожидает `/workspace`) и применяет workload policy. Код задаёт read-only root filesystem, `cap_drop`, `no-new-privileges`, PID/resource limits, configured CPU/memory и network mode (`none` или constrained `bridge` destinations). Точные limits — deployment configuration, frontend их не устанавливает.

В workspace используются `/workspace/projects/<task_id>` (checkout task), `.qwen-home` (Qwen home), `.agent/skills/<skill_id>` (materialized Skills), `.uar-tools` (trusted packaged helper/MCP assets). Repository task живёт в `/workspace/projects/<task_id>`.

Реализовано в: `adapters/docker_runtime.py`, `adapters/docker_agent_qwen.py`, `adapters/docker_development_workspace.py`.

## 6. Qwen Code, session и TLS

`DockerAgentQwenRunner` выполняет Qwen Code через Docker `exec` в уже принадлежащем lifecycle Agent. Это не второй Agent runtime. `QwenSessionAdapter` хранит session state/history на trusted host-side storage, materializes/synchronizes Qwen home into workspace, запускает runner и синхронизирует state/transcript обратно. Это нужно для recovery/истории без превращения HTTP request в session.

Для корпоративного CA используется `UAR_SFERA_CA_CERT_PATH`: configured PEM переносится в Agent workspace, а Node получает `NODE_EXTRA_CA_CERTS`. В ветке `server-deploy-vllm` `NODE_EXTRA_CA_CERTS` добавляется даже при отсутствии Sfera task operations. Это добавляет trust anchor, не отключая TLS verification. Не передавайте frontend-ом PEM, API key или сертификат.

Реализовано в: `adapters/qwen_session.py`, `adapters/docker_agent_qwen.py`, `configuration.py`.

## 7. Skills, Tools и workspace operations

Skill — instruction package, Tool — capability, workspace operation — deterministic backend action. Это разные сущности.

`skill.json` описывает package metadata/capabilities; `SKILL.md` — instructions. `SkillPackageCatalog` discovers built-in package directories and can include configured filesystem registry. При create lifecycle validates requested IDs; отсутствующий ID даёт lifecycle error `skill_unavailable`. Selected IDs materialize into `/workspace/.agent/skills/<skill_id>` and are exposed via `UAR_AGENT_SKILL_PACKAGES`.

Java development guard требует фактический `JAVA_SKILLS`:

```json
{
  "request_id": "java-agent-001",
  "skills": [
    "requirements-clarification",
    "development-planning",
    "java-project-setup",
    "java-implementation",
    "code-review"
  ],
  "tools": []
}
```

`DevelopmentWorkflow.begin()` также требует пустые `tools`; произвольные Tool capability для development Agent не допускаются. Наличие `code-review` Skill не означает запуск `REVIEWING`: текущий code-only workflow этот stage не вызывает.

Реализовано в: `adapters/skill_packages.py`, `adapters/filesystem_skill_registry.py`, `application/development_workflow.py`.

## 8. DevelopmentTask и текущий code-only workflow

DevelopmentTask связывает один READY Agent, specification, build system, repository request и trace. Для URL-repository `GitRequest` создаёт deterministic working branch `uar/<task_id>`; frontend не генерирует target branch.

```text
Agent
  +-- DevelopmentTask task_id
        +-- base branch (например master)
        +-- working branch uar/<task_id>
```

Фактический URL workflow:

```text
CREATED -> CLONING_REPOSITORY -> ANALYZING_REQUIREMENTS -> PLANNING
-> PREPARING_WORKSPACE -> IMPLEMENTING -> COMMITTING -> PUSHING -> COMPLETED
```

`CLONING_REPOSITORY` выполняется только при `repository_url`. Requirements может перейти в `WAITING_FOR_CLARIFICATION`; после `POST /development-tasks/{task_id}/clarifications` flow возобновляется. `PREPARING_WORKSPACE` создаёт project; local-only выполняет init. `IMPLEMENTING` просит LLM вернуть строго JSON `files`, validates paths/content и вызывает write. `COMMITTING` выполняет inventory/status/add/diff/commit; `PUSHING` только при `publish=true`.

**В текущей ветке НЕ выполняются `mvn test`, `mvn package`, `TESTING`, `REVIEWING` и `FIXING`.** `build_system` остаётся формальным validated field (`maven`/`gradle`) в API/domain, а `max_fix_attempts` остаётся в DTO/result model, но code-only путь их не использует для build/review loop. Enum и transitions legacy states сохранены; UI может встретить их в старом state/trace, но current workflow их не вызывает.

Реализовано в: `application/development_workflow.py`, `domain/development_task.py`, `development_http.py`.

## 9. LLM против deterministic layer

| LLM | Deterministic UAR |
| --- | --- |
| requirements reasoning, questions | admission/claim/release Agent |
| plan | state transitions, cancellation, trace |
| JSON code/file proposals | path/content validation and file writing |
| review proposal code exists but is not used in code-only flow | branch naming, Git add/diff/commit/push |
|  | credentials, SSH host policy, network and redaction |

LLM output никогда не является командой shell или Git transport request. `WorkspaceOperation` — закрытый set: `PREPARE`, `INIT`, `CLONE`, `WRITE`, `INVENTORY`, `STATUS`, `DIFF`, `ADD`, `COMMIT`, `PUSH`, `TEST`, `PACKAGE`; current production Agent rejects remote clone/push workspace operations. Chain writing: `LLM JSON -> DevelopmentWorkflow -> WorkspaceRequest -> DockerDevelopmentWorkspaceAdapter -> workspace-operations.mjs -> filesystem`.

Внутри Agent local credential-free Git делает status/diff/add/commit. JS helper uses fixed argv, disables credential helper/prompts/hooks/system/global config, validates project paths and Git config; arbitrary shell API отсутствует.

Реализовано в: `application/ports/development_workspace.py`, `adapters/docker_development_workspace.py`, `agent_image/workspace-operations.mjs`.

## 10. Trusted Git и push security

Remote Git отделён от Agent. Перед clone/push `DockerTrustedGitAdapter` pause Agent, создаёт disposable helper, монтирует workspace volume и только helper получает read-only key/known_hosts. Helper удаляется до Agent resume; при неуверенной cleanup операция fail-closed.

```text
Orchestrator -> pause Agent -> helper(volume + key ro + known_hosts ro)
             -> clone/push -> remove helper -> resume Agent
```

Push не исполняет credential-bearing Git в Agent-owned `.git`: helper создаёт fresh bare repo, копирует только bounded regular Git objects, проверяет commit, fetches base branch, проверяет `merge-base --is-ancestor`, затем пушит exact SHA в `refs/heads/uar/<task_id>` без force/delete/rewrite. SSH uses strict host checking and explicit known_hosts. Private key не попадает в Agent environment, volume, prompt, trace, transcript или HTTP.

`repository_url` — canonical SSH URL; only configured endpoint allowlist, user `git`, path `.git`, no password/query/fragment/local/scp-like form. Stable codes включают `repository_url_invalid`, `repository_not_allowed`, `repository_auth_failed`, `repository_not_found`, `repository_clone_failed`, `repository_branch_not_found`, `repository_push_failed`, `repository_conflict`, `repository_unavailable`.

Реализовано в: `application/ports/trusted_git.py`, `adapters/docker_trusted_git.py`, `adapters/trusted_git_helper.py`.

## 11. Конфигурация

Все значения server-side; frontend их не читает и не передаёт. Значения ниже — examples без secrets.

| Группа | ENV | Required / пример | Назначение |
| --- | --- | --- | --- |
| API | `UAR_API_HOST`, `UAR_API_PORT` | required / `127.0.0.1`, `8080` | bind API |
| Runtime | `UAR_RUNTIME_DRIVER` | required / `kata` | `docker` или `kata` |
| Runtime | `UAR_DOCKER_WORKLOAD_KEY`, `UAR_DOCKER_WORKLOAD_IMAGE`, `UAR_DOCKER_WORKLOAD_COMMAND_JSON`, `UAR_DOCKER_WORKLOAD_USER`, `UAR_DOCKER_WORKSPACE_TARGET` | required | Agent workload and `/workspace` target |
| Resources | `UAR_AGENT_CPU_CORES`, `UAR_AGENT_MEMORY_BYTES`, operation/readiness timeouts | required | Agent resource/time policy |
| Network | `UAR_DOCKER_NETWORK_MODE`, host/port for bridge | required / `none` or `bridge` | constrained Agent network |
| Qwen | `UAR_QWEN_BASE_URL`, `UAR_QWEN_MODEL`, `UAR_QWEN_API_KEY_SECRET_ID`, `UAR_QWEN_API_KEY` | required / `https://example.internal/v1`, `qwen3.8-27b`, `<API_KEY>` | OpenAI-compatible inference |
| Qwen | `UAR_QWEN_SESSION_STORAGE_ROOT`, reasoning/time settings | required/optional | persistent host-side sessions |
| Skills | `UAR_SKILL_REGISTRY_ROOT` | optional | filesystem registry |
| Development | `UAR_JAVA_DEVELOPMENT_ENABLED` | boolean | enables development routes/workflow |
| Git | `UAR_GIT_SSH_PRIVATE_KEY_FILE`, `UAR_GIT_SSH_KNOWN_HOSTS_FILE`, `UAR_GIT_SSH_ALLOWED_ENDPOINTS`, `UAR_GIT_HELPER_IMAGE` | configured production URL path / `/path/to/ssh/private_key` | trusted helper only |
| Git | `UAR_GIT_AUTHOR_NAME`, `UAR_GIT_AUTHOR_EMAIL` | optional | per-task Git identity |
| Sfera/TLS | `UAR_SFERA_BASE_URL`, `UAR_SFERA_CA_CERT_PATH` | optional / `/path/to/corporate-ca-bundle.pem` | Sfera and extra CA |
| Sfera | username/password, secret IDs, owner, limits | optional | server-side tool configuration |
| Streaming | `UAR_STREAM_HEARTBEAT_SECONDS`, `UAR_STREAM_SEND_TIMEOUT_SECONDS` | optional | SSE heartbeat/send bounds |

Реализовано в: `configuration.py`.

## 12. Запуск, health и base URL

Current R&D deployment URL: `http://10.228.64.200:8080`. Это environment-specific address; frontend must make base URL configurable.

`run-orchestrator.sh` требует `UAR_API_HOST` и `UAR_API_PORT`, then executes `.venv/bin/python -m uvicorn universal_agent_runtime.http_api:create_application_from_environment --factory`. On server documented deployment directory is `~/universal-agent-runtime-sd2`.

```bash
cd ~/universal-agent-runtime-sd2
set -a
. ./.env
set +a
bash ./run-orchestrator.sh                    # foreground
# or: nohup bash ./run-orchestrator.sh > /tmp/uar-orchestrator.log 2>&1 &
curl --fail http://127.0.0.1:8080/healthz    # {"status":"ok"}
curl --fail http://127.0.0.1:8080/readyz     # {"status":"ready","runtime_driver":"kata"}
```

`healthz` is liveness only; `readyz` confirms configured application readiness and reports selected driver, not that a particular Agent is READY.

## 13. Full frontend happy path

1. `GET /healthz`, then `GET /readyz`.
2. `POST /agents`; save `agent_id`.
3. `POST /agents/{agent_id}/start`; poll `GET /agents/{agent_id}` until `state="READY"`.
4. `POST /agents/{agent_id}/development-tasks`; save `task_id`.
5. `POST /ag-ui/development-tasks/{task_id}/run` with SSE; process frames until terminal event.
6. `GET /development-tasks/{task_id}` for authoritative state/result; `GET /development-tasks/{task_id}/trace` for timeline.
7. On completion optionally `POST /agents/{agent_id}/stop`, then `DELETE /agents/{agent_id}` (204/no body).

### Create Agent

```bash
curl -sS -X POST "$BASE_URL/agents" -H 'Content-Type: application/json' -d '{
  "request_id":"ui-java-001",
  "skills":["requirements-clarification","development-planning","java-project-setup","java-implementation","code-review"],
  "tools":[]
}'
```

Response fields: `agent_id`, `workspace_id`, `session_id`, `state`, `configuration` (`workload`, `cpu_cores`, `memory_bytes`, `skills`, `tools`), `runtime`, `failure`, `conversation_recovery_required`.

```ts
const agent = await api.createAgent({ request_id: crypto.randomUUID(), skills: JAVA_SKILLS, tools: [] });
await api.startAgent(agent.agent_id);
```

### Create task

```json
{
  "specification": "Create Hello.java with a main method.",
  "build_system": "maven",
  "repository_url": "ssh://git@example.internal:30022/team/example.git",
  "base_branch": "master",
  "publish": true,
  "max_fix_attempts": 2
}
```

`repository_url` and `base_branch` are validated; working branch is backend-owned. `local_only:true` is an explicit no-URL mode; exactly one of repository URL or local-only is required by current route. Response contains `task_id`, `agent_id`, `state`, `version`, `questions`, `plan`, `fix_attempts`, `cancel_requested`, `failure_code`, `result`.

### Run task and SSE

```bash
curl -N -X POST "$BASE_URL/ag-ui/development-tasks/$TASK_ID/run" \
  -H 'Accept: text/event-stream' -H 'Content-Type: application/json' \
  -d '{"threadId":"ui-thread-1","runId":"ui-run-1"}'
```

Development body is exactly `threadId` and `runId`, each 1..128 `[A-Za-z0-9_-]+`. `200` only means stream established. Success requires terminal `RUN_FINISHED` and authoritative task `state="COMPLETED"`; failure may be `RUN_ERROR` despite HTTP 200.

Development SSE uses `data: <JSON>\n\n` and comments `: keep-alive`. Events: `RUN_STARTED` (`threadId`, `runId`); `STEP_STARTED`/`STEP_FINISHED` (`stepName`); `CUSTOM` (`name`, trace `value`); final `TEXT_MESSAGE_START` (`messageId`, `role`), `TEXT_MESSAGE_CONTENT` (`messageId`, `delta`), `TEXT_MESSAGE_END` (`messageId`), `RUN_FINISHED`; failure `RUN_ERROR` (`code`, `message`). `CUSTOM.value` equals trace event; phase starts yield STEP_STARTED, later phase status yields STEP_FINISHED.

```ts
export async function runDevelopmentTask(url: string, taskId: string, signal?: AbortSignal) {
  const response = await fetch(`${url}/ag-ui/development-tasks/${taskId}/run`, {
    method: "POST", signal, headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ threadId: crypto.randomUUID().replaceAll("-", ""), runId: crypto.randomUUID().replaceAll("-", "") })
  });
  if (!response.ok || !response.body) throw new Error(`SSE start failed: ${response.status}`);
  const reader = response.body.getReader(), decoder = new TextDecoder(); let buffered = "";
  for (;;) {
    const part = await reader.read(); if (part.done) break;
    buffered += decoder.decode(part.value, { stream: true });
    const frames = buffered.split("\n\n"); buffered = frames.pop()!;
    for (const frame of frames) {
      const line = frame.split("\n").find(x => x.startsWith("data: "));
      if (!line) continue; const event = JSON.parse(line.slice(6)) as AGUIEvent;
      onEvent(event); // update timeline/text; RUN_ERROR is terminal failure
    }
  }
  // On network disconnect: GET task/trace; do not resubmit blindly.
}
```

`EventSource` is unsuitable because this endpoint requires POST. Use fetch/ReadableStream; AbortController cancels local read. Server keeps owned work on disconnect, so reconnect by reading task/trace rather than replaying run.

### Task and trace

`GET /development-tasks/{task_id}` returns the fields above. `result`, when completed, contains `branch`, `commit_id`, `files`, `checks`, `repository_id`, `published`, `execution_backend`, `repository_url`, `base_branch`, `working_branch`.

`GET /development-tasks/{task_id}/trace` returns `{task_id,agent_id,state,events,summary,failure_code}`. Each event has `sequence,type,phase,status,step_name,attempt,summary,data`. Render it as append-only timeline; do not derive security details from it.

## 14. UI state and errors

Suggested UI states:

```text
NO_AGENT -> CREATING_AGENT -> AGENT_STOPPED -> STARTING_AGENT -> AGENT_READY
-> CREATING_TASK -> TASK_CREATED -> RUNNING_TASK -> TASK_COMPLETED | TASK_FAILED
```

Store `agent_id`, `task_id`, current Agent/task state, final stream status, trace and last safe error code. Do not store keys, CA material, raw diagnostics or SSH commands.

| Backend state | UI meaning / action |
| --- | --- |
| Agent `READY` | enable task creation/run |
| Agent `BUSY` | show task running; disable conflicting actions |
| Agent `STOPPED` | offer Start or Delete |
| Agent `FAILED` | show safe failure code; offer retry only when `retryable=true` |
| Task `CREATED` | ready to run |
| `WAITING_FOR_CLARIFICATION` | show `questions`, submit clarification |
| `COMPLETED` | authoritative success; show result/trace |
| `FAILED` / `CANCELLED` | show `failure_code`, trace and safe retry action |
| legacy `TESTING`, `REVIEWING`, `FIXING` | valid enum but not emitted by current code-only workflow |

Common errors: `agent_unavailable` means Agent missing/not READY/recovery required or development guard failed; `skill_unavailable` means catalog/registry does not contain requested skill; `invalid_model_result` is rejected LLM JSON; `repository_*` codes identify URL/allowlist/auth/branch/push issues; `publication_rejected` blocks unauthorized remote action; `workspace_rejected` blocks paths/config; `operation_failed`, `operation_timeout`, `output_limit` are bounded operation failures. Display code plus a non-sensitive explanation. Retry only when response explicitly says retryable, or after user fixes input/configuration; do not claim universal retry policy.

The development guard specifically requires existing Agent, `READY`, no recovery requirement, all `JAVA_SKILLS`, and empty tools. This is the generic cause of `agent_unavailable` for a development run.

## 15. Frontend contracts and examples

```ts
export interface Agent { agent_id:string; workspace_id:string; session_id:string;
  state:"CREATING"|"STARTING"|"READY"|"BUSY"|"STOPPING"|"STOPPED"|"FAILED";
  configuration:{workload:string;cpu_cores:number;memory_bytes:number;skills:string[];tools:string[]};
  runtime:{execution:string;readiness:string}|null; failure:unknown|null; conversation_recovery_required:boolean; }
export interface DevelopmentTask { task_id:string; agent_id:string; state:string; version:number;
  questions:string[]; plan:string[]; fix_attempts:number; cancel_requested:boolean; failure_code:string|null; result:DevelopmentResult|null; }
export interface DevelopmentResult { branch:string;commit_id:string;files:string[];checks:string[];published:boolean;
  execution_backend:string;repository_url:string|null;base_branch:string|null;working_branch:string|null; }
export interface TraceEntry { sequence:number;type:string;phase:string;status:string;step_name:string|null;attempt:number;summary:string;data:Record<string,unknown>; }
export type AGUIEvent = {type:string;[key:string]:unknown};
export const api = { createAgent:(body:unknown)=>post<Agent>("/agents",body), startAgent:(id:string)=>post<Agent>(`/agents/${id}/start`), getAgent:(id:string)=>get<Agent>(`/agents/${id}`), createDevelopmentTask:(id:string,body:unknown)=>post<DevelopmentTask>(`/agents/${id}/development-tasks`,body), getDevelopmentTask:(id:string)=>get<DevelopmentTask>(`/development-tasks/${id}`), getDevelopmentTrace:(id:string)=>get<{events:TraceEntry[]}>(`/development-tasks/${id}/trace`) };
```

React integration should keep server state separate from stream UI state: `useAgent` polls GET Agent while starting; `useDevelopmentTask` owns AbortController, appends trace/SSE events and refreshes task after terminal/disconnect. Do not make backend state transitions in React; API remains authoritative.

End-to-end example: create Java Agent with the listed five skills and empty tools; start/poll READY; create task with `specification:"Create one Hello.java"`, a valid repository URL and `publish:true`; save fictional `task_id:"taskabc"`; POST run; collect CUSTOM `repository_clone_*`, phase events and `git_commit`; after `RUN_FINISHED`, GET task and require `COMPLETED`, then show `result.commit_id` and `working_branch:"uar/taskabc"`.

## 16. Security, limitations and troubleshooting

```text
Trusted: Orchestrator/configuration
Untrusted: Agent process, LLM output, cloned repository
Privileged/trusted: disposable Git helper with credentials
```

Frontend must never manage Kata, call LiteLLM directly, generate SSH commands, choose remote branch, pass API keys, perform Git push, expose Qwen API key/Sfera credentials/SSH key/CA private material, or interpret HTTP 200 as task success. API keys stay server-side; private SSH key stays helper-side.

Safe server diagnostics: `curl http://127.0.0.1:8080/healthz`, `curl http://127.0.0.1:8080/readyz`, `ss -ltnp 'sport = :8080'`, `docker ps --filter label=io.universal-agent-runtime.managed=true`, `tail /tmp/uar-orchestrator-sd2.log`. Do not use `cat .env`, print keys/certificates, `curl -k`, or disable TLS. For TLS/auth/push failure, show safe code, inspect trace and hand off to server operator.

Known current limits: control-plane Agent/task repositories are in-memory; orchestrator restart can lose their records even if Docker/Kata resources still exist. Development is Java-specific and intentionally code-only; it skips build/test/review/fix. Production Docker/Kata/Qwen/Sfera verification is server-only. Configuration and environment setup remain operator work.

## 17. Source code map and quick start

| File | Responsibility |
| --- | --- |
| `http_api.py` | FastAPI Agent/lifecycle/chat/AG-UI routes |
| `development_http.py` | task create/get/trace/run/cancel routes |
| `ag_ui.py` | AG-UI SSE event projection |
| `composition.py` | dependency composition |
| `configuration.py` | validated environment settings |
| `domain/agent.py` | Agent public states |
| `domain/development_task.py` | task states/transitions/result |
| `agent_lifecycle.py` | lifecycle service |
| `agent_chat.py` | owned chat turns |
| `development_tasks.py` | task ownership/trace persistence |
| `development_workflow.py` | code-only task orchestration |
| `docker_runtime.py` | managed Docker/Kata runtime |
| `docker_agent_qwen.py` | Qwen Docker exec and task env |
| `qwen_session.py` | Qwen session/transcript state |
| `docker_development_workspace.py` | closed workspace adapter |
| `workspace-operations.mjs` | filesystem/local Git operations |
| `docker_trusted_git.py` | helper lifecycle and mounts |
| `trusted_git_helper.py` | SSH clone/push hardening |
| `trusted_git.py` | Git request/URL/branch contract |
| `skill_packages.py` | skill catalog/materialization |

Quick Start: (1) configure frontend base URL; (2) GET `readyz`; (3) POST Java Agent; (4) POST start and poll READY; (5) POST development task; (6) POST AG-UI SSE run; (7) wait `RUN_FINISHED`; (8) GET task and require `COMPLETED`; (9) show trace/result; (10) stop/delete Agent when no longer needed.

## 18. Glossary

**UAR/Orchestrator** — deterministic backend control plane. **Agent** — isolated runtime plus workspace. **Kata** — configured container runtime for Agent isolation. **Qwen Code** — process executed inside Agent. **LLM/LiteLLM/vLLM** — inference chain/model service. **Skill** — packaged instruction; **Tool** — explicit capability; **Workspace** — Agent volume. **DevelopmentTask** — controlled implementation workflow. **Trusted Git/Git Helper** — credential-bearing disposable remote Git boundary. **AG-UI** — event contract; **SSE** — HTTP event stream transport.
