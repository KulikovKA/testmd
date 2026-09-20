# TASK-000: verification boundary and server procedure

Работа выполнена локально на Windows. Сервер **10.228.64.200** не использовался.
Все server checks ниже имеют статус **SERVER_VERIFICATION_REQUIRED**.
Поле Actual заполняет пользователь после выполнения; локальные doubles не
подтверждают работоспособность Docker/Kata/Qwen/LLM на сервере.

## Reference comparison

- Repository: `https://github.com/KulikovKA/testmd`, branch `server-deploy-2`.
- Reference: `78e2b41dba7188ca37b96eefe1464d13d9c06f21`,
  `Add real-time AG-UI text streaming`.
- До изменений локальный HEAD равнялся reference; `git diff <reference> -- src tests`
  был пуст. Объект commit доступен локально, загрузка/сброс ветки не требовались.
- Сохранены low-level `exec_create / exec_start(stream=True, demux=True) /
  exec_inspect`, Qwen partial-message flags, JSONL parser, streaming port,
  bounded `TurnDeltas` (16), SSE transport и committed-only legacy endpoint.
- Дополнения: redaction с удержанием только возможного secret suffix, одинаковые
  правила для final text, size-only coalescing (32 characters), проверка
  согласованности до application history commit, detach при сбое отправки headers,
  rollback при ошибке callback, включая последний flush.

## Local verification

Проверяемые Python suites используют explicit ports, fake Docker clients,
deterministic Qwen runners, временные каталоги и FastAPI test clients.
Node suite запускает локальный HTTP double и MCP subprocess, без реального Sfera.

| Category | Scope / result |
| --- | --- |
| LOCAL_VERIFIED | Python 3.12.14 и Node 24.19.0 найдены в существующем Codex runtime. Зависимости проекта установлены в `.runtime/verification-venv`; системное ПО не устанавливалось. |
| LOCAL_VERIFIED | Git 2.53.0.windows.1; `java -version` и `javac -version`: OpenJDK 17.0.18. |
| LOCAL_VERIFIED | Полный Python suite и Node suite: результаты в разделе Commands/results ниже. |
| LOCAL_NOT_AVAILABLE | `mvn` и `gradle` отсутствуют в PATH. Старая `.venv` ссылается на удалённый Python 3.11; для проверок создано отдельное окружение. |
| LOCAL_NOT_AVAILABLE | Java integration, temporary bare Git repository, FakeRepositoryPlatformAdapter и DevelopmentTask state-machine suites в этой ревизии отсутствуют. Это не выполненные проверки; TASK-000 не добавляет Java/DevelopmentTask architecture. |
| SERVER_VERIFICATION_REQUIRED | Реальные Docker + Kata + KVM, Agent image, Qwen Code, внешний LLM, серверная сеть, credentials, Sfera и deployment. |

Commands/results (PowerShell из корня проекта):

```powershell
.runtime/verification-venv/Scripts/python.exe -m pytest -q
& 'C:/Users/Kirill/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe' --test tests/test_sfera_mcp.mjs
git diff --check
```

Python: **80 passed**, 0 failed, 0 skipped (2 warnings: Starlette/AnyIO
deprecation and an intentionally duplicated ZIP entry in the archive rejection test).
Node: **66 passed**, 0 failed, 0 skipped.
`git diff --check`: passed. Ruff passed on the modified streaming modules/tests
with `SIM117` excluded (existing nested test context-manager style).
Server command blocks: Bash `-n` and Python `ast.parse` passed locally;
the commands themselves were not executed.

Coverage: incremental UTF-8/JSONL parsing; final result validation; exact delta
concatenation/event order/message identity; history only after success; failure
and protocol mismatch → RUN_ERROR; bounded provisional data; redaction across
every two-chunk boundary and one-character chunks, prefix-related secrets;
coalescing and final flush; callback rollback; disconnect/header failure/send
timeout; heartbeat; HTTP headers; legacy committed response/replay rejection;
existing lifecycle, skill archives, authorization, path/security and MCP suites.

## Server setup — 10.228.64.200 only

Выполнять Bash-команды на сервере под учётной записью с разрешённым Docker/sudo.
Используется deployment path из README; если на сервере другой путь, задать его
в `DEPLOY_ROOT`. Сначала доставить туда проверенные изменения обычным процессом
deployment. Установку пакета и restart выполнять в согласованное окно: repository
Agents находится в памяти процесса. Не копировать локальную Windows `.venv`.

```bash
export DEPLOY_ROOT=/opt/universal-agent-runtime
cd "$DEPLOY_ROOT"
export BASE_URL=http://127.0.0.1:8080
export PYTHON="$DEPLOY_ROOT/.venv/bin/python"
```

### Check 1 — deployment, Docker/Kata/KVM and real Agent image

Why server-only: Linux Docker daemon, Kata runtime, `/dev/kvm`, server image и
service environment отсутствуют в локальном test runtime.

Changed components: `application/text_stream.py`, `adapters/qwen_session.py`,
`application/agent_chat.py`, `application/ports/agent_interaction.py`, `ag_ui.py`,
`http_api.py`. Существующие deployment scripts и Docker/Kata configuration не менялись.

Commands:

```bash
sudo -u uar "$PYTHON" -m pip install .
sudo systemctl restart universal-agent-runtime
sudo systemctl is-active universal-agent-runtime
curl --fail --silent --show-error "$BASE_URL/healthz"
curl --fail --silent --show-error "$BASE_URL/readyz"
docker info --format '{{json .Runtimes}}'
test -c /dev/kvm
sudo -u uar test -r /dev/kvm
sudo -u uar test -w /dev/kvm

export AGENT_ID=$(curl --fail --silent --show-error "$BASE_URL/agents" \
  -H 'Content-Type: application/json' \
  -d "{\"request_id\":\"stream-check-$(date +%s)\",\"skills\":[],\"tools\":[]}" \
  | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["agent_id"])')
test -n "$AGENT_ID"
curl --fail --silent --show-error -X POST "$BASE_URL/agents/$AGENT_ID/start"
export CONTAINER_ID=$(docker ps -q \
  --filter label=io.universal-agent-runtime.managed=true \
  --filter "label=io.universal-agent-runtime.agent=$AGENT_ID")
test -n "$CONTAINER_ID"
docker inspect --format '{{.HostConfig.Runtime}} {{.State.Status}} {{.Config.Image}} {{.Image}}' "$CONTAINER_ID"
docker exec "$CONTAINER_ID" qwen --version
sudo ps -eo pid,ppid,comm | grep -E 'qemu|cloud-hypervisor|kata'
```

Expected: service active, `/readyz` reports `kata`, Agent READY, exactly one
managed container running with runtime `kata`, expected deployment image digest,
Qwen version matches the image (reference pin 0.23.1). Verify a real hypervisor
process for this Agent through the server Kata runtime diagnostics; Docker
`running` alone does not prove microVM creation. `/dev/kvm` access must match the
actual runtime service user if it differs from `uar`.

Actual: PENDING SERVER VERIFICATION

### Check 2 — real Qwen / LLM → incremental AG-UI, history and coalescing

Why server-only: actual Qwen process, configured external LLM, credentials and
Docker socket. Local parser/runner doubles verify logic only.

Changed components: all streaming files listed in Check 1;
`tests/test_ag_ui_streaming.py`, `tests/test_ag_ui.py` provide local coverage.

Commands (same shell; Agent from Check 1 must have an empty history):

```bash
"$PYTHON" - <<'PY'
import json, os, time, urllib.request, uuid
base, agent = os.environ['BASE_URL'], os.environ['AGENT_ID']
def get(path):
    with urllib.request.urlopen(base + path, timeout=30) as response:
        return json.load(response)
payload = dict(threadId='verify-thread', runId=str(uuid.uuid4()), state={},
    messages=[dict(id='verify-user', role='user', content=
        'Explain containerization in twelve numbered points, at least 600 characters in total.')],
    tools=[], context=[], forwardedProps={})
assert get(f'/agents/{agent}/messages')['messages'] == []
request = urllib.request.Request(base + f'/ag-ui/agents/{agent}/run',
    data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
events, live = [], False
started = time.monotonic()
with urllib.request.urlopen(request, timeout=1000) as response:
    assert response.headers.get_content_type() == 'text/event-stream'
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Accel-Buffering'] == 'no'
    for raw in response:
        if not raw.startswith(b'data: '):
            continue
        event = json.loads(raw[6:])
        events.append(event)
        print(round(time.monotonic() - started, 3), event['type'],
              len(event.get('delta', '')), flush=True)
        if event['type'] == 'TEXT_MESSAGE_CONTENT' and not live:
            state = get(f'/agents/{agent}')['state']
            history = get(f'/agents/{agent}/messages')['messages']
            live = state == 'BUSY' and history == []
types = [e['type'] for e in events]
deltas = [e['delta'] for e in events if e['type'] == 'TEXT_MESSAGE_CONTENT']
assert len(deltas) > 1, types
assert types == ['RUN_STARTED', 'TEXT_MESSAGE_START'] + ['TEXT_MESSAGE_CONTENT'] * len(deltas) + ['TEXT_MESSAGE_END', 'RUN_FINISHED']
assert live, 'No provisional text observed before commit; repeat with a longer prompt and investigate buffering'
history = get(f'/agents/{agent}/messages')['messages']
assert len(history) == 2
assert ''.join(deltas) == history[1]['content']
assert {e['messageId'] for e in events if 'messageId' in e} == {history[1]['message_id']}
assert all(len(delta) >= 32 for delta in deltas[:-1]), list(map(len, deltas))
print('PASS: incremental output before commit, exact final/history match; chunk sizes:', list(map(len, deltas)))
PY
```

Expected: several incremental TEXT_MESSAGE_CONTENT events before inference/turn
completion; only new text per delta; chunks >=32 characters except final tail;
strict lifecycle order and exact concatenation to the single committed assistant
message. If the model generates insufficient text or the live observation races
with completion, the check is inconclusive until repeated successfully.
Also repeat through the actual client/reverse-proxy route: loopback verification
does not prove that the proxy forwards SSE without buffering.

Actual: PENDING SERVER VERIFICATION

### Check 3 — disconnected client, owned turn, legacy endpoint

Why server-only: real socket closure while Docker exec and inference continue.
Changed components: `ag_ui.py`, `http_api.py`, `application/agent_chat.py`.

Commands (after Check 2):

```bash
"$PYTHON" - <<'PY'
import json, os, time, urllib.request, uuid
base, agent = os.environ['BASE_URL'], os.environ['AGENT_ID']
def get(path):
    with urllib.request.urlopen(base + path, timeout=30) as response:
        return json.load(response)
before = len(get(f'/agents/{agent}/messages')['messages'])
payload = dict(threadId='detach-thread', runId=str(uuid.uuid4()), state={},
    messages=[dict(id='detach-user', role='user', content='Explain container networking in twelve detailed points.')],
    tools=[], context=[], forwardedProps={})
request = urllib.request.Request(base + f'/ag-ui/agents/{agent}/run',
    data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(request, timeout=1000) as response:
    for raw in response:
        if raw.startswith(b'data: ') and json.loads(raw[6:])['type'] == 'TEXT_MESSAGE_CONTENT':
            assert get(f'/agents/{agent}')['state'] == 'BUSY', 'Turn already ended: repeat with a longer prompt'
            break
    else:
        raise AssertionError('No text before disconnect')
deadline = time.monotonic() + 1000
while time.monotonic() < deadline:
    state = get(f'/agents/{agent}')['state']
    if state != 'BUSY':
        assert state == 'READY', state
        assert len(get(f'/agents/{agent}/messages')['messages']) == before + 2
        print('PASS: disconnected owned turn committed without queue deadlock')
        break
    time.sleep(1)
else:
    raise AssertionError('Owned turn remains BUSY after inference deadline')
PY
curl --fail --no-buffer "$BASE_URL/agents/$AGENT_ID/messages/stream" \
  -H 'Content-Type: application/json' -d '{"content":"Reply with one short sentence."}'
```

Expected: disconnect does not leave Agent permanently BUSY; final history gains
exactly two messages. Legacy SSE remains `started` (`committed_response`), one
`content`, `completed`; it does not emit provisional deltas.

Actual: PENDING SERVER VERIFICATION

### Check 4 — server network isolation and LLM reachability

Why server-only: real network/firewall rules and controlled allowed/denied
destinations are server configuration. Docker `bridge` alone is not an egress
allowlist and local fakes do not prove network enforcement.

Changed components: none in Docker/network policy; Check 2 exercises the
unchanged production adapter with the updated streaming pipeline.

Commands:

```bash
docker inspect --format '{{.HostConfig.NetworkMode}} {{json .NetworkSettings.Networks}} {{json .HostConfig.PortBindings}}' "$CONTAINER_ID"
docker exec "$CONTAINER_ID" node -e 'const net=require("net");const u=new URL(process.env.QWEN_OLLAMA_BASE_URL);const s=net.connect(Number(u.port||(u.protocol==="https:"?443:80)),u.hostname);s.setTimeout(5000);s.on("connect",()=>{console.log("allowed LLM reachable");s.destroy()});s.on("timeout",()=>{s.destroy();process.exitCode=1});s.on("error",()=>{process.exitCode=1})'
# Set to a live, operator-controlled destination outside the Agent allowlist.
read -r -p 'Controlled denied host: ' DENIED_HOST
read -r -p 'Controlled denied port: ' DENIED_PORT
docker exec "$CONTAINER_ID" node -e 'const net=require("net");const s=net.connect(Number(process.argv[2]),process.argv[1]);s.setTimeout(5000);s.on("connect",()=>{console.error("FAIL: denied destination reachable");s.destroy();process.exitCode=1});s.on("timeout",()=>{console.log("denied destination blocked");s.destroy()});s.on("error",()=>console.log("denied destination unreachable"))' "$DENIED_HOST" "$DENIED_PORT"
```

Expected: configured LLM reachable and Check 2 authenticates successfully;
denied destination blocked; no published Agent ports. Correlate the denied
probe with server firewall policy/logs and independently confirm that the
controlled destination is live. An unreachable/offline destination is not proof
of isolation. Record failure if the deployment has no required egress enforcement.

Actual: PENDING SERVER VERIFICATION

### Check 5 — real Sfera credentials / read-only Task MCP

Why server-only: corporate Sfera endpoint, trust chain and credentials. Local
Node tests use a stub. There is no Sfera Code repository adapter in this revision;
real Sfera Code verification remains pending its implementation, not passed.

Changed components: no Sfera protocol changes; `qwen_session.py` streaming
redaction also covers injected Sfera username/password.

Commands (use an existing Task that the service account is allowed to read):

```bash
export SFERA_AGENT_ID=$(curl --fail --silent --show-error "$BASE_URL/agents" \
  -H 'Content-Type: application/json' \
  -d "{\"request_id\":\"sfera-read-check-$(date +%s)\",\"skills\":[],\"tools\":[\"get_task\"]}" \
  | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["agent_id"])')
curl --fail --silent --show-error -X POST "$BASE_URL/agents/$SFERA_AGENT_ID/start"
read -r -p 'Existing readable Task number: ' TASK_NUMBER
export TASK_NUMBER
"$PYTHON" -c 'import json,os; print(json.dumps({"threadId":"sfera-check","runId":"sfera-read","state":{},"messages":[{"id":"read-task","role":"user","content":"Read Task " + os.environ["TASK_NUMBER"] + " using get_task and summarize it. Do not mutate anything."}],"tools":[],"context":[],"forwardedProps":{}}))' \
  | curl --fail --no-buffer "$BASE_URL/ag-ui/agents/$SFERA_AGENT_ID/run" \
      -H 'Content-Type: application/json' --data-binary @-
curl --fail --silent --show-error "$BASE_URL/agents/$SFERA_AGENT_ID/debug-report"
```

Expected: successful read using actual `get_task` (confirm debug-report tool
observability), correct Task summary, no mutations, no credentials/cookies in
SSE/history/debug-report. Do not ask the model to reveal real credentials to test
redaction. Split-secret/prefix/coalescer checks are deterministic local tests;
any additional live redaction exercise must use a disposable synthetic canary
in an isolated server test configuration.

Actual: PENDING SERVER VERIFICATION

### Check 6 — real adapter failure → RUN_ERROR, no history commit

Why server-only: disappearance of the running production Agent container before
Docker exec. Local tests cover parser/protocol/callback failures separately.

Changed components: `adapters/qwen_session.py`, `application/agent_chat.py`,
`ag_ui.py`. This check stops only its newly created disposable test Agent.

Commands:

```bash
export FAILURE_AGENT_ID=$(curl --fail --silent --show-error "$BASE_URL/agents" \
  -H 'Content-Type: application/json' \
  -d "{\"request_id\":\"failure-check-$(date +%s)\",\"skills\":[],\"tools\":[]}" \
  | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["agent_id"])')
test -n "$FAILURE_AGENT_ID"
curl --fail --silent --show-error -X POST "$BASE_URL/agents/$FAILURE_AGENT_ID/start"
FAILURE_CONTAINER_ID=$(docker ps -q \
  --filter label=io.universal-agent-runtime.managed=true \
  --filter "label=io.universal-agent-runtime.agent=$FAILURE_AGENT_ID")
test -n "$FAILURE_CONTAINER_ID"
docker stop "$FAILURE_CONTAINER_ID"
curl --fail --no-buffer "$BASE_URL/ag-ui/agents/$FAILURE_AGENT_ID/run" \
  -H 'Content-Type: application/json' \
  -d '{"threadId":"failure-thread","runId":"failure-run","state":{},"messages":[{"id":"failure-message","role":"user","content":"Say hello."}],"tools":[],"context":[],"forwardedProps":{}}'
curl --fail --silent --show-error "$BASE_URL/agents/$FAILURE_AGENT_ID/messages"
```

Expected: `RUN_STARTED`, then sanitized `RUN_ERROR`, no `RUN_FINISHED`, no new
history messages. This verifies runtime failure, not a simulated successful LLM run.

Actual: PENDING SERVER VERIFICATION

## Cleanup

Only after recording observations, remove the test Agents created above:

```bash
curl --fail --silent --show-error -X DELETE "$BASE_URL/agents/$AGENT_ID"
if [ -n "${SFERA_AGENT_ID:-}" ]; then
  curl --fail --silent --show-error -X DELETE "$BASE_URL/agents/$SFERA_AGENT_ID"
fi
if [ -n "${FAILURE_AGENT_ID:-}" ]; then
  curl --fail --silent --show-error -X DELETE "$BASE_URL/agents/$FAILURE_AGENT_ID"
fi
```

Do not fill any server Actual field with a local test result.
