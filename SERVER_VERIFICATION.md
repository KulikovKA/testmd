# Проверки на сервере

## Проверка 11. Existing repository / trusted Git (текущий этап)

**SERVER_VERIFICATION_REQUIRED**. Локально проверены ports, Docker doubles,
HTTP/AG-UI, native bare Git и JDK 17 fixture; реальный SSH endpoint, host keys,
Docker volume/Kata pause, helper image и Qwen доступны только на 10.228.64.200.
Изменены: `trusted_git.py`, `docker_trusted_git.py`, `trusted_git_helper.py`,
`git_helper/Dockerfile*`, workflow/domain/HTTP/config/composition,
`agent_image/workspace-operations.mjs`, Postman и тесты.

- `LOCAL_VERIFIED`: targeted 44 passed; full Python 147 passed (2 existing
  warnings, no skips); Node 75 passed; Ruff, diff/secret-pattern scan и синтаксис
  Bash/Python команд ниже проверены. Native Git и JDK 17 fixtures выполнены.
- `LOCAL_NOT_AVAILABLE`: Java 21, Maven, Gradle; системное ПО не устанавливалось.
- `SERVER_VERIFICATION_REQUIRED`: все проверки 11a–11c, включая настоящий image,
  Kata pause/resume, SSH/Sfera transport, host pinning и Java/Qwen E2E.

### 11a. Доставка и отдельный helper image

Оператор: завершить активные задачи и штатно остановить подтверждённый процесс
Orchestrator перед перезапуском (см. проверку 1). Команды не меняют `.env` и
сохраняют серверный `.dockerignore`; при конфликте merge остановиться, не делать
reset/restore/checkout файла. Выполнять в Bash:

```bash
cd ~/universal-agent-runtime-sd2
cp -p agent_image/.dockerignore "/tmp/uar-agent-dockerignore-$(date +%s)"
git fetch origin server-deploy-2
git merge --ff-only origin/server-deploy-2
grep -Fx '!capabilities.mjs' agent_image/.dockerignore
grep -Fx '!workspace-operations.mjs' agent_image/.dockerignore
docker build -f git_helper/Dockerfile -t uar-git-helper:local .
docker build -t uar-agent:java21-sd2 agent_image
set -a
. ./.env
set +a
export UAR_GIT_SSH_PRIVATE_KEY_FILE="$HOME/.ssh/uar_sfera_code_ed25519"
export UAR_GIT_SSH_KNOWN_HOSTS_FILE="$HOME/.ssh/known_hosts"
export UAR_GIT_SSH_ALLOWED_ENDPOINTS=10.228.84.126:30022
export UAR_GIT_HELPER_IMAGE=uar-git-helper:local
export UAR_GIT_AUTHOR_NAME='Admin Sferovich'
export UAR_GIT_AUTHOR_EMAIL=foo@mail.sfera-t1.ru
export UAR_DOCKER_WORKLOAD_IMAGE=uar-agent:java21-sd2
export UAR_JAVA_DEVELOPMENT_ENABLED=true
test -r "$UAR_GIT_SSH_PRIVATE_KEY_FILE"
test -s "$UAR_GIT_SSH_KNOWN_HOSTS_FILE"
nohup bash ./run-orchestrator.sh > /tmp/uar-orchestrator-sd2.log 2>&1 &
curl --fail --silent --show-error http://127.0.0.1:8080/readyz
```

`known_hosts` должен уже содержать независимо проверенный host key для
`[10.228.84.126]:30022`. Не принимать автоматически результат ssh-keyscan.
Ключ не читать/не выводить; private key расположен вне workspace.
Expected: API ready, runtime_driver=kata; оба образа собраны; локальный diff
`.dockerignore` сохранён. Helper build context включает только пять Python-файлов.
Actual: PENDING SERVER VERIFICATION

### 11b. Реальный workflow, SHA, trace и credential boundary

Запуск создаёт только рабочую ветку `uar/<task-id>` в существующем тестовом repo.
```bash
export BASE_URL=http://127.0.0.1:8080
export PYTHON="$PWD/.venv/bin/python"
export AGENT_ID=$(curl -fsS "$BASE_URL/agents" -H 'Content-Type: application/json' \
  -d '{"request_id":"existing-repo-check","skills":["requirements-clarification","development-planning","java-project-setup","java-implementation","java-testing","code-review"],"tools":[]}' \
  | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["agent_id"])')
curl -fsS -X POST "$BASE_URL/agents/$AGENT_ID/start"
export TASK_ID=$(curl -fsS "$BASE_URL/agents/$AGENT_ID/development-tasks" \
  -H 'Content-Type: application/json' \
  -d '{"specification":"Add a Java 21 Maven Calculator.add(int,int) library with JUnit 5 tests for positive, negative and zero inputs. Preserve existing repository files. Add .gitignore for target. No external services.","build_system":"maven","repository_url":"ssh://git@10.228.84.126:30022/test/test.git","base_branch":"master","publish":true,"max_fix_attempts":2}' \
  | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["task_id"])')
curl -fNsS "$BASE_URL/ag-ui/development-tasks/$TASK_ID/run" \
  -H 'Content-Type: application/json' -d '{"threadId":"trusted-git","runId":"server-check"}' \
  > /tmp/uar-existing-repo-events.sse
curl -fsS "$BASE_URL/development-tasks/$TASK_ID" > /tmp/uar-existing-repo-result.json
curl -fsS "$BASE_URL/development-tasks/$TASK_ID/trace" > /tmp/uar-existing-repo-trace.json
curl -fsS "$BASE_URL/agents/$AGENT_ID/llm-turns?limit=10" > /tmp/uar-existing-repo-turns.json
"$PYTHON" - <<'PY'
import json, os, pathlib, subprocess, docker
result = json.loads(pathlib.Path('/tmp/uar-existing-repo-result.json').read_text())
assert result['state'] == 'COMPLETED', (result['state'], result['failure_code'])
r = result['result']
assert r['published'] and r['base_branch'] == 'master'
assert r['working_branch'] == 'uar/' + os.environ['TASK_ID']
assert r['execution_backend'] == 'agent'
events = [json.loads(line[6:]) for line in pathlib.Path('/tmp/uar-existing-repo-events.sse').read_text().splitlines() if line.startswith('data: ')]
assert events[-1]['type'] == 'RUN_FINISHED'
assert {'STEP_STARTED', 'STEP_FINISHED', 'CUSTOM', 'TEXT_MESSAGE_CONTENT'} <= {e['type'] for e in events}
trace = json.loads(pathlib.Path('/tmp/uar-existing-repo-trace.json').read_text())
assert {'repository_clone_finished','branch_created','git_commit','repository_push_finished'} <= {e['type'] for e in trace['events']}
for filename in ('result.json','trace.json','turns.json','events.sse'):
    text = pathlib.Path('/tmp/uar-existing-repo-' + filename).read_text()
    assert os.environ['UAR_GIT_SSH_PRIVATE_KEY_FILE'] not in text
    assert 'BEGIN OPENSSH PRIVATE KEY' not in text and 'GIT_SSH_COMMAND' not in text
client = docker.from_env()
assert not client.containers.list(all=True, filters={'label':'io.universal-agent-runtime.git-helper=true'})
agent = client.containers.list(filters={'label':'io.universal-agent-runtime.agent=' + os.environ['AGENT_ID']})[0]
assert not agent.attrs['State']['Paused']
assert all(m['Source'] != os.environ['UAR_GIT_SSH_PRIVATE_KEY_FILE'] for m in agent.attrs['Mounts'])
assert not any(v.startswith('UAR_GIT_SSH_') or v.startswith('SSH_AUTH_SOCK=') for v in agent.attrs['Config']['Env'])
# Independent read-only remote SHA check; no key contents read by Python.
import shlex
ssh = ['ssh','-F','/dev/null','-i',os.environ['UAR_GIT_SSH_PRIVATE_KEY_FILE'],
       '-o','IdentitiesOnly=yes','-o','IdentityAgent=none','-o','BatchMode=yes',
       '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+os.environ['UAR_GIT_SSH_KNOWN_HOSTS_FILE'],
       '-o','GlobalKnownHostsFile=/dev/null','-o','ForwardAgent=no']
env = {**os.environ, 'GIT_SSH_COMMAND': shlex.join(ssh), 'GIT_TERMINAL_PROMPT':'0'}
check = subprocess.run(['git','ls-remote','--',r['repository_url'],'refs/heads/'+r['working_branch']], env=env, capture_output=True, text=True, timeout=30)
assert check.returncode == 0, 'remote verification failed (raw stderr suppressed)'
assert check.stdout.split()[0] == r['commit_id']
print('remote SHA == local SHA; helper removed; Agent boundary verified')
PY
```

Expected: настоящий Maven test/package, review, commit, published=true, remote SHA
совпадает; incremental STEP/CUSTOM до конечного текста; private key/mount paths
отсутствуют в Agent env/mounts и публичных данных. Если возникли clarification
или build/network ошибки — записать фактический результат, не считать E2E успешным.
Actual: PENDING SERVER VERIFICATION

### 11c. Fail-closed host verification и helper cleanup

На READY Agent без активной задачи выполнить только clone с пустым временным
known_hosts (remote не изменяется). Настоящий known_hosts и ключ не меняются.
```bash
"$PYTHON" - <<'PY'
import asyncio, os, tempfile, docker
from universal_agent_runtime.adapters.docker_trusted_git import DockerTrustedGitAdapter, TrustedGitSettings
from universal_agent_runtime.application.ports.trusted_git import GitRequest
from universal_agent_runtime.domain.development_task import DevelopmentFailure
from universal_agent_runtime.domain.identifiers import AgentId
async def check():
    with tempfile.NamedTemporaryFile() as empty_hosts:
        client = docker.from_env(timeout=310)
        adapter = DockerTrustedGitAdapter(client, TrustedGitSettings(
            os.environ['UAR_GIT_SSH_PRIVATE_KEY_FILE'], empty_hosts.name,
            ('10.228.84.126:30022',), 'uar-git-helper:local'))
        try:
            await adapter.clone(AgentId(os.environ['AGENT_ID']), GitRequest(
                'verify-host-key','ssh://git@10.228.84.126:30022/test/test.git','master','uar/verify-host-key'))
        except DevelopmentFailure as error:
            assert error.code == 'repository_unavailable', error.code
        else:
            raise AssertionError('unknown host key unexpectedly accepted')
        assert not client.containers.list(all=True, filters={'label':'io.universal-agent-runtime.git-helper=true'})
        agent = client.containers.list(filters={'label':'io.universal-agent-runtime.agent='+os.environ['AGENT_ID']})[0]
        assert not agent.attrs['State']['Paused']
        adapter.close()
        print('unknown host rejected; helper removed; Agent resumed')
asyncio.run(check())
PY
```

Expected: repository_unavailable, helper удалён, Agent возобновлён. Для отмены
задачи во время настоящего clone/push отправить в другом shell:
`curl -fsS -X POST "$BASE_URL/development-tasks/$TASK_ID/cancel"` и дождаться
окончания текущей операции; затем повторить assertions helper/Paused из 11b.
Disconnect SSE не отменяет owned task. Уже завершённый push не откатывается.
При недоступном Docker cleanup Agent остаётся paused; оператор сначала проверяет
helper по label и устраняет причину. Recovery после аварийной остановки процесса
не автоматизирован (process-local task registry).
Actual: PENDING SERVER VERIFICATION

Сервер: **10.228.64.200**. Каталог развёртывания:
`/home/kkulikov/universal-agent-runtime-sd2`.
Orchestrator запускается вручную через `nohup`; systemd не используется.

Команды ниже — инструкции пользователю. Агент не выполнял их на сервере,
не читал и не выводил `.env`, не добавлял credentials.
Все серверные проверки: **SERVER_VERIFICATION_REQUIRED**.
Фактические результаты заполняются только после выполнения на сервере.
2026-09-21 пользователь сообщил, что baseline `3d5cabe` уже проверен:
Java image/Kata, READY, Qwen, обычный AG-UI incremental streaming и Java Skills.
Это сведения пользователя, не результаты запуска агентом. Новые observability
endpoints и STEP/CUSTOM streaming требуют отдельной проверки 10.

## Локальная граница

- `LOCAL_VERIFIED`: принятый baseline `fc601e0f930382c2e6989f4889e01f408993540e`;
  на предыдущем этапе прошли 80 Python-тестов и 66 Node-тестов.
  Актуальные результаты Java Agent work: `PROJECT_STATE_JAVA_AGENT.md`.
- `LOCAL_VERIFIED`: Git 2.53.0, Java/javac 17.0.18, Python 3.12.14, Node 24.19.0.
- `LOCAL_NOT_AVAILABLE`: Java 21, Maven и Gradle не доступны в текущем PATH.
  Системное ПО локально не устанавливалось.
- `SERVER_VERIFICATION_REQUIRED`: настоящий Docker/Kata/KVM, production Agent
  image, серверный Qwen Code, внешний LLM/Ollama, серверная сеть и Сфера Код.

## Проверка 1. Ручной запуск Orchestrator

Почему нужна серверная среда: рабочие конфигурация, Python environment,
Docker daemon и сетевые назначения находятся на сервере.

Компоненты: `run-orchestrator.sh`, `configuration.py`, `composition.py`.
Исправлена инструкция под фактическое развёртывание; механизм запуска не менялся.

Сначала доставить изменения обычным процессом развёртывания. Следующие команды
выполняются пользователем в Bash на сервере. Перед повторным запуском определить
PID слушающего процесса; не запускать второй Orchestrator на том же порту.

```bash
cd /home/kkulikov/universal-agent-runtime-sd2
export DEPLOY_ROOT="$PWD"
export BASE_URL=http://127.0.0.1:8080
export PYTHON="$DEPLOY_ROOT/.venv/bin/python"
ss -ltnp 'sport = :8080'
```

Если нужен перезапуск, завершить только подтверждённый PID текущего Orchestrator
после окончания активных задач. Реестр Agent находится в памяти процесса.
После освобождения порта выполнить:

```bash
cd /home/kkulikov/universal-agent-runtime-sd2
set -a
. ./.env
set +a
nohup bash ./run-orchestrator.sh \
  > /tmp/uar-orchestrator-sd2.log 2>&1 &
ss -ltnp 'sport = :8080'
curl -sS http://127.0.0.1:8080/readyz
curl --fail --silent --show-error http://127.0.0.1:8080/healthz
```

Ожидается: один процесс слушает порт 8080; `/readyz` возвращает
`{"status":"ready","runtime_driver":"kata"}`; `/healthz` возвращает `{"status":"ok"}`.
Загрузка `.env` выполняется только оператором сервера для запуска, без вывода
содержимого. Лог: `/tmp/uar-orchestrator-sd2.log`.

Фактический результат: PENDING SERVER VERIFICATION

## Проверка 2. Docker, Kata, KVM и настоящий образ Agent

Почему нужна серверная среда: локальные doubles не создают microVM и не
проверяют `/dev/kvm` или production image.

Компоненты: `agent_image/Dockerfile`, `agent_image/agent-runtime`,
`adapters/docker_runtime.py`, `adapters/docker_agent_qwen.py`.

```bash
docker info --format '{{json .Runtimes}}'
test -c /dev/kvm
ls -l /dev/kvm
export AGENT_ID=$(curl --fail --silent --show-error "$BASE_URL/agents" \
  -H 'Content-Type: application/json' \
  -d "{\"request_id\":\"server-check-$(date +%s)\",\"skills\":[],\"tools\":[]}" \
  | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["agent_id"])')
test -n "$AGENT_ID"
curl --fail --silent --show-error -X POST "$BASE_URL/agents/$AGENT_ID/start"
export CONTAINER_ID=$(docker ps -q \
  --filter label=io.universal-agent-runtime.managed=true \
  --filter "label=io.universal-agent-runtime.agent=$AGENT_ID")
test -n "$CONTAINER_ID"
docker inspect --format '{{.HostConfig.Runtime}} {{.State.Status}} {{.Config.Image}} {{.Image}}' "$CONTAINER_ID"
docker exec "$CONTAINER_ID" qwen --version
ps -eo pid,ppid,comm | grep -E 'qemu|cloud-hypervisor|kata'
```

Ожидается: ровно один управляемый контейнер этого Agent, runtime `kata`,
состояние `running`, ожидаемые image digest и версия Qwen. Подтвердить реальный
hypervisor для этого Agent средствами диагностики установленного Kata.
Docker `running` сам по себе не доказывает создание microVM.
Проверять права KVM у фактического пользователя runtime.

Фактический результат: PENDING SERVER VERIFICATION

## Проверка 3. Qwen / LLM → AG-UI и история

Почему нужна серверная среда: реальный Qwen process, Docker exec, внешний LLM,
серверные credentials и путь через reverse proxy.

Компоненты: существующие `qwen_session.py`, `agent_chat.py`, `text_stream.py`,
`ag_ui.py`, `http_api.py`. Streaming baseline принят; рефакторинг не требуется.

В той же оболочке использовать новый Agent из проверки 2 с пустой историей:

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

Ожидается: несколько новых дельт до завершения inference, точная конкатенация
в final response, одна пара сообщений в истории; последний chunk может быть
короче 32 символов. Слишком короткий ответ или гонка наблюдения требует повтора.
Повторить через фактический reverse proxy: loopback не подтверждает отсутствие
буферизации на клиентском маршруте.

Фактический результат: PENDING SERVER VERIFICATION

## Проверка 4. Disconnect, legacy endpoint и ошибка Agent

Почему нужна серверная среда: разрыв реального соединения во время inference.
Компоненты: существующие `ag_ui.py`, `http_streaming.py`, `agent_chat.py`.

Запустить длинный запрос, прервать только curl после нескольких дельт (Ctrl+C),
затем опрашивать Agent до окончания серверного wall-time limit:

```bash
curl --fail --no-buffer "$BASE_URL/ag-ui/agents/$AGENT_ID/run" \
  -H 'Content-Type: application/json' \
  -d '{"threadId":"detach","runId":"detach-1","state":{},"messages":[{"id":"d1","role":"user","content":"Объясни сети контейнеров в двенадцати подробных пунктах."}],"tools":[],"context":[],"forwardedProps":{}}'
curl --fail --silent --show-error "$BASE_URL/agents/$AGENT_ID"
curl --fail --silent --show-error "$BASE_URL/agents/$AGENT_ID/messages"
curl --fail --no-buffer "$BASE_URL/agents/$AGENT_ID/messages/stream" \
  -H 'Content-Type: application/json' -d '{"content":"Ответь одним предложением."}'
```

Ожидается: отключённый клиент не оставляет Agent в BUSY; при успешном turn история
увеличивается ровно на два сообщения. Legacy сохраняет `started`, один committed
`content`, `completed`. Ошибки Qwen проверять только на отдельном тестовом Agent
в согласованной серверной конфигурации: `RUN_ERROR`, без `RUN_FINISHED` и без
новых сообщений истории. Не останавливать контейнеры рабочих Agent.

Фактический результат: PENDING SERVER VERIFICATION

## Проверка 5. Серверная сеть и credentials

Почему нужна серверная среда: реальные правила сети и защищённые назначения.
Компоненты: `docker_runtime.py`, `composition.py`, настройки deployment.

```bash
docker inspect --format '{{.HostConfig.NetworkMode}} {{json .NetworkSettings.Networks}} {{json .HostConfig.PortBindings}}' "$CONTAINER_ID"
docker exec "$CONTAINER_ID" node -e 'const net=require("net");const u=new URL(process.env.QWEN_OLLAMA_BASE_URL);const s=net.connect(Number(u.port||(u.protocol==="https:"?443:80)),u.hostname);s.setTimeout(5000);s.on("connect",()=>{console.log("LLM доступен");s.destroy()});s.on("timeout",()=>{s.destroy();process.exitCode=1});s.on("error",()=>{process.exitCode=1})'
```

Ожидается: разрешённый LLM доступен, Agent не публикует порты. Проверить запрет
доступа к заранее согласованному живому назначению вне allowlist с сопоставлением
правил firewall. `bridge` и недоступный произвольный адрес не доказывают изоляцию.
Credentials проверяются успешным штатным запросом; не выводить Docker environment,
`.env`, cookies или токены и не просить модель раскрыть их. Для live redaction
допустим только синтетический canary в отдельной тестовой конфигурации.

Фактический результат: PENDING SERVER VERIFICATION

## Проверка 6. Сфера Код

Почему нужна серверная среда: реальные endpoints, authentication, permissions,
namespace/group model и clone URL пока не предоставлены.
Компоненты: будущая граница `RepositoryPlatformPort` и адаптер Сфера Код.

Команды пока не определяются: их можно записать только после получения
подтверждённого API-контракта. Существующий Task MCP не является Сфера Код.

Ожидается: provider-neutral application, проверенные metadata/clone information,
Git clone/push штатным Git, отсутствие credentials в prompt/transcript/AG-UI,
отчётах, файлах проекта и commits. Fake adapter не подтверждает реальную интеграцию.

Фактический результат: PENDING SERVER VERIFICATION

## Проверка 7. Java-capable образ

Статус: SERVER_VERIFICATION_REQUIRED. Локально проверен только контракт
capability probe через doubles. Изменены `agent_image/Dockerfile`,
`agent_image/agent-runtime`, добавлен `agent_image/capabilities.mjs`.
Java 21, Maven 3.9.11 и Gradle 8.14.3 копируются из отдельных toolchain stages;
Qwen digest сохраняется. Совместимость библиотек с Qwen base проверяет build-time
probe, но локальная сборка образа не заявляется. Tags toolchain images могут
обновляться; после проверки зафиксировать фактические digests в deployment.

```bash
cd /home/kkulikov/universal-agent-runtime-sd2
bash ./build-agent-image.sh uar-agent:java21-sd2
docker run --rm --runtime kata --read-only --user 10001:10001 \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --tmpfs /workspace:rw,uid=10001,gid=10001,size=256m \
  uar-agent:java21-sd2 capabilities
```

Ожидается: `ready=true`, Java/javac major=21, доступны Git/Maven/Gradle/bash/
curl/unzip/Qwen/Node. Затем оператор выбирает проверенный образ в защищённой
серверной конфигурации и повторяет ручной запуск и проверки 2–4. Смена network
policy ради загрузки образов или build dependencies автоматически не выполняется.

Фактический результат: PENDING SERVER VERIFICATION

## Проверка 8. Настоящий Java workflow через существующий Agent

Статус: SERVER_VERIFICATION_REQUIRED. Нужны настоящий Java 21 образ, Kata,
Qwen/LLM и разрешённые серверной политикой Maven/Gradle dependencies. Локальные
doubles и JDK 17 fixture это не подтверждают. Изменены `development_workflow.py`,
`development_http.py`, `composition.py`, `configuration.py`, lifecycle admission,
добавлены domain/ports/adapters, шесть Skills и `workspace-operations.mjs`.

После проверки 7 оператор выбирает новый образ и включает Java workflow при
ручном запуске. Порт должен быть свободен; активные задачи сначала завершить.
Сеть не расширять автоматически; при недоступных dependencies зафиксировать
фактическую ошибку и отдельно согласовать network policy.

```bash
cd /home/kkulikov/universal-agent-runtime-sd2
set -a
. ./.env
set +a
export UAR_DOCKER_WORKLOAD_IMAGE=uar-agent:java21-sd2
export UAR_JAVA_DEVELOPMENT_ENABLED=true
nohup bash ./run-orchestrator.sh > /tmp/uar-orchestrator-sd2.log 2>&1 &
ss -ltnp 'sport = :8080'
curl -sS http://127.0.0.1:8080/readyz
```

Запустить оба варианта через Python серверного environment. Скрипт создаёт по
одному тестовому Agent на build system, выводит только идентификаторы, состояния,
число дельт и проверенный commit. Repository отсутствует: публикация в реальную
Сфера Код здесь не заявляется. На clarification скрипт останавливается с
идентификаторами; ответ отправляется отдельно и run повторяется.

```bash
cd /home/kkulikov/universal-agent-runtime-sd2
.venv/bin/python - <<'PY'
import json, urllib.request, uuid
base = 'http://127.0.0.1:8080'
def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=2400) as response:
        return json.load(response)
skills = ['requirements-clarification', 'development-planning', 'java-project-setup',
          'java-implementation', 'java-testing', 'code-review']
for build in ('maven', 'gradle'):
    agent = call('POST', '/agents', {'request_id': 'java-' + uuid.uuid4().hex,
                                   'skills': skills, 'tools': []})['agent_id']
    call('POST', f'/agents/{agent}/start')
    task = call('POST', f'/agents/{agent}/development-tasks', {
        'specification': 'Создай небольшую Java 21 библиотеку Calculator.add(int,int), '
                         'тесты JUnit 5 для положительных, отрицательных чисел и нуля, '
                         'build configuration и .gitignore. Без внешних сервисов и публикации.',
        'build_system': build, 'branch': 'main', 'local_only': True, 'publish': False,
        'max_fix_attempts': 2})['task_id']
    print('AGENT_ID=' + agent, 'TASK_ID=' + task, 'BUILD=' + build, flush=True)
    req = urllib.request.Request(base + f'/ag-ui/development-tasks/{task}/run',
        data=json.dumps({'threadId': task, 'runId': uuid.uuid4().hex}).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    events = []
    with urllib.request.urlopen(req, timeout=2400) as response:
        for line in response:
            if line.startswith(b'data: '):
                events.append(json.loads(line[6:]))
    result = call('GET', f'/development-tasks/{task}')
    print('STATE=' + result['state'], 'FAILURE=' + str(result['failure_code']))
    assert result['state'] == 'COMPLETED', 'Проверить failure_code или отправить clarification'
    assert events[-1]['type'] == 'RUN_FINISHED'
    assert not any(e['type'] == 'RUN_ERROR' for e in events)
    deltas = [e['delta'] for e in events if e['type'] == 'TEXT_MESSAGE_CONTENT']
    assert len(deltas) == 1  # Development выдаёт только финальную сводку.
    assert any(e['type'] == 'STEP_STARTED' for e in events)
    assert any(e['type'] == 'CUSTOM' for e in events)
    assert result['result']['execution_backend'] == 'agent'
    assert result['result']['published'] is False
    assert len(result['result']['checks']) == 2
    print('DELTAS=' + str(len(deltas)), 'COMMIT=' + result['result']['commit_id'])
PY
```

При `WAITING_FOR_CLARIFICATION`, используя выведенный `TASK_ID`:

```bash
curl --fail -sS -H 'Content-Type: application/json' \
  -d '{"answer":"Библиотека Java 21, JUnit 5, публикация не нужна; остальные решения выбери самостоятельно."}' \
  "http://127.0.0.1:8080/development-tasks/$TASK_ID/clarifications"
curl --fail -N -H 'Content-Type: application/json' \
  -d '{"threadId":"java-verification","runId":"clarified-run"}' \
  "http://127.0.0.1:8080/ag-ui/development-tasks/$TASK_ID/run"
curl --fail -sS "http://127.0.0.1:8080/development-tasks/$TASK_ID"
```

Независимая проверка каждого выведенного Agent/task: подставить его идентификаторы
в `AGENT_ID` и `TASK_ID`, выбрать ровно один running container.

```bash
CONTAINER_ID=$(docker ps -q --filter "label=io.universal-agent-runtime.agent=$AGENT_ID")
test -n "$CONTAINER_ID"
docker inspect --format '{{.HostConfig.Runtime}} {{.Config.User}}' "$CONTAINER_ID"
docker exec --user 10001:10001 "$CONTAINER_ID" agent-runtime capabilities
docker exec --user 10001:10001 --workdir "/workspace/projects/$TASK_ID" \
  "$CONTAINER_ID" git -c core.hooksPath=/usr/local/share/uar/empty-hooks rev-parse HEAD
docker exec --user 10001:10001 --workdir "/workspace/projects/$TASK_ID" \
  "$CONTAINER_ID" git -c core.hooksPath=/usr/local/share/uar/empty-hooks status --porcelain
docker exec --user 10001:10001 "$CONTAINER_ID" node -e \
  'const fs=require("fs"),p=require("path"),root="/workspace/projects/"+process.argv[1];let n=0;function walk(d){if(!fs.existsSync(d))return;for(const e of fs.readdirSync(d,{withFileTypes:true})){const f=p.join(d,e.name);if(e.isDirectory())walk(f);else if(e.name.endsWith(".xml")){const t=fs.readFileSync(f,"utf8");for(const m of t.matchAll(/<testsuite\b[^>]*\btests="(\d+)"/g))n+=Number(m[1]);if(/\b(?:failures|errors)="[1-9]/.test(t))throw Error("test failure")}}}walk(p.join(root,"target/surefire-reports"));walk(p.join(root,"build/test-results/test"));if(n<1)throw Error("no executed tests");console.log("executed tests="+n)' "$TASK_ID"
```

Ожидается: runtime kata, UID/GID 10001, Java 21; build и тесты действительно
прошли, XML содержит ненулевое число тестов без failures/errors; native HEAD
совпадает с result.commit_id; рабочее дерево чистое; присутствуют промежуточные
AG-UI STEP/CUSTOM события. При неудаче записать безопасный failure_code и состояние, не
выводить environment, Qwen native state, cookies или credentials.

Для disconnect использовать отдельную такую же задачу и оборвать `curl -N`
после первых STEP/CUSTOM событий. Затем GET должен показать продолжение до
COMPLETED/FAILED без зависания очереди. Для cancellation на отдельной задаче
выполнить `POST /development-tasks/$TASK_ID/cancel` во время TESTING: до
завершения операции владелец Agent сохраняется, затем состояние CANCELLED и
нового commit/push нет. Во время владения chat/stop/delete возвращают conflict.
Это проверяется отдельно от локальных deterministic tests.

Фактический результат: PENDING SERVER VERIFICATION

## Проверка 9. Repository transport и будущие credentials

Статус: SERVER_VERIFICATION_REQUIRED. Изменены `repository_platform.py`,
`repository_credentials.py`, `repository_access.py`, `sfera_code_repository.py`,
`docker_development_workspace.py`, native Git helper. Настоящий provider и broker
не реализуются без контракта Сфера Код. Локально доказан только fake API + bare Git.

Текущий fail-closed результат проверяется тем же workflow: добавить в create DTO
`"repository":{"namespace":"verification","name":"java-check"},"publish":true`.
Ожидается FAILED с `repository_unavailable`, RUN_ERROR, без native clone/push и
без отправки HTTP-запросов к придуманному provider. Реальные URL/headers/credentials
и команды live clone/push здесь будут добавлены после получения контракта и
согласования доверенного broker. Не помещать их в DTO, Agent или Skills.
`UAR_REPOSITORY_ALLOWED_HOSTS` не разрешает authenticated transport и не открывает
firewall. Изменения security boundary требуют отдельного согласования.

Фактический результат: PENDING SERVER VERIFICATION

## Проверка 10. Development observability: live, trace, LLM turns

Статус: SERVER_VERIFICATION_REQUIRED. Причина: реальный Qwen transcript и
Java/Kata runtime находятся на 10.228.64.200. Локально проверены fixtures,
test clients, временный Git и протокол; новый server E2E не запускался.

Компоненты: `development_trace.py`, `development_tasks.py`,
`development_workflow.py`, `ag_ui.py`, `development_http.py`, `http_api.py`,
`qwen_observability.py`, `qwen_session.py`, read-only interaction port,
`composition.py`, `workspace-operations.mjs` (фактический exit_code).
Контракт frontend: `docs/DEVELOPMENT_OBSERVABILITY.md`.

Серверный локальный фикс `agent_image/.dockerignore` сохранить. В этой задаче
файл не менялся. Перед доставкой новой версии сделать резервную копию только
этого diff, затем убедиться, что изменение сохранилось. Не применять к нему
checkout/reset/restore. Доставка изменений и выполнение команд ниже — оператором.

```bash
cd /home/kkulikov/universal-agent-runtime-sd2
git status --short -- agent_image/.dockerignore
git diff --binary -- agent_image/.dockerignore > /tmp/uar-sd2-dockerignore-observability.patch
# После доставки новой версии повторить проверку: локальный фикс должен остаться.
git diff -- agent_image/.dockerignore
bash ./build-agent-image.sh uar-agent:observability-sd2
```

Для обновления exit_code нужен свежий image; старый возвращает null, что
сохраняет совместимость. После завершения активных задач перезапустить только
подтверждённый Orchestrator PID. Реестры в памяти будут потеряны. Выполнить в
отдельном Bash shell; `.env` загружает оператор, содержимое не выводится.

```bash
cd /home/kkulikov/universal-agent-runtime-sd2
set -e
set -o pipefail
ss -ltnp 'sport = :8080'
read -r -p 'PID Orchestrator из ss (пусто, если порт свободен): ' UAR_OLD_PID
if [ -n "$UAR_OLD_PID" ]; then
  case "$UAR_OLD_PID" in *[!0-9]*) exit 1 ;; esac
  case "$(ps -p "$UAR_OLD_PID" -o args=)" in
    *universal_agent_runtime.http_api:create_application_from_environment*) kill -TERM "$UAR_OLD_PID" ;;
    *) printf '%s\n' 'PID не подтверждён как UAR Orchestrator'; exit 1 ;;
  esac
  for UAR_WAIT in $(seq 1 30); do
    kill -0 "$UAR_OLD_PID" 2>/dev/null || break
    sleep 1
  done
  if kill -0 "$UAR_OLD_PID" 2>/dev/null; then
    printf '%s\n' 'Процесс ещё работает; новый Orchestrator не запущен'; exit 1
  fi
fi
set -a
. ./.env
set +a
export UAR_JAVA_DEVELOPMENT_ENABLED=true
export UAR_DOCKER_WORKLOAD_IMAGE=uar-agent:observability-sd2
nohup bash ./run-orchestrator.sh > /tmp/uar-orchestrator-sd2.log 2>&1 &
export BASE_URL=http://127.0.0.1:8080
for UAR_WAIT in $(seq 1 30); do
  curl --fail -sS "$BASE_URL/readyz" && break
  sleep 1
done
curl --fail -sS "$BASE_URL/readyz"
```

Создать и запустить свежий Agent, создать development task без repository:

```bash
export AGENT_ID=$(curl --fail -sS -H 'Content-Type: application/json' \
  -d "{\"request_id\":\"obs-$(date +%s)\",\"skills\":[\"requirements-clarification\",\"development-planning\",\"java-project-setup\",\"java-implementation\",\"java-testing\",\"code-review\"],\"tools\":[]}" \
  "$BASE_URL/agents" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["agent_id"])')
curl --fail -sS -X POST "$BASE_URL/agents/$AGENT_ID/start"
export TASK_ID=$(curl --fail -sS -H 'Content-Type: application/json' \
  -d '{"specification":"Создай Java 21 библиотеку Calculator.add(int,int), JUnit 5 тесты для положительных, отрицательных чисел и нуля, Maven configuration и .gitignore. Без внешних сервисов и публикации. Остальные решения выбери самостоятельно.","build_system":"maven","branch":"main","local_only":true,"publish":false,"max_fix_attempts":2}' \
  "$BASE_URL/agents/$AGENT_ID/development-tasks" \
  | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["task_id"])')
printf 'AGENT_ID=%s\nTASK_ID=%s\n' "$AGENT_ID" "$TASK_ID"
curl --fail -N -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d "{\"threadId\":\"$TASK_ID\",\"runId\":\"obs-$(date +%s)\"}" \
  "$BASE_URL/ag-ui/development-tasks/$TASK_ID/run" | tee /tmp/uar-observability-run.sse
curl --fail -sS "$BASE_URL/development-tasks/$TASK_ID/trace" | tee /tmp/uar-observability-trace.json
curl --fail -sS "$BASE_URL/agents/$AGENT_ID/llm-turns?after=0&limit=10"
```

При WAITING_FOR_CLARIFICATION выполнить clarification из проверки 8 и новый
run. RUN_FINISHED завершает запуск, состояние задачи при этом может быть waiting.
Для Gradle повторить создание свежего Agent/task с build_system=gradle и
соответствующим ТЗ. Команды с credentials и доступ к Сфера Код здесь не нужны.

Независимая сверка live и trace одного запуска, без повторного запуска workflow:

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
events = [json.loads(line[6:]) for line in Path('/tmp/uar-observability-run.sse').read_text().splitlines() if line.startswith('data: ')]
trace = json.loads(Path('/tmp/uar-observability-trace.json').read_text())
facts = [e['value'] for e in events if e['type'] == 'CUSTOM']
assert facts == trace['events'][-len(facts):]
assert events[0]['type'] == 'RUN_STARTED'
assert trace['state'] == 'COMPLETED', trace['failure_code']
assert events[-1]['type'] == 'RUN_FINISHED'
active = None
for event in events:
    if event['type'] == 'STEP_STARTED':
        assert active is None
        active = event['stepName']
    elif event['type'] == 'STEP_FINISHED':
        assert event['stepName'] == active
        active = None
assert active is None
text = [e['delta'] for e in events if e['type'] == 'TEXT_MESSAGE_CONTENT']
assert len(text) == 1 and '"files"' not in text[0]
commands = [e for e in facts if e['type'] == 'command_finished']
assert commands and all(e['data']['exit_code'] is not None for e in commands)
print('STEP/CUSTOM, trace, final summary и реальные exit codes проверены')
PY
```

Ожидается: требования → план → файлы → test/package → review → commit,
парные STEP, корректные attempt при fix-loop, идентичные CUSTOM и REST записи.
В TEXT_MESSAGE_CONTENT нет phase JSON; /llm-turns показывает current messages,
конечный видимый assistant, корректный порядок и доступную numeric telemetry.
System prompt/reasoning/raw environment/tool payload отсутствуют. Не выводить
реальные secrets для проверки; использовать только синтетический canary в
отдельной тестовой конфигурации.

Повторить disconnect после STEP_STARTED на отдельной задаче: GET /trace должен
продолжать обновляться. /llm-turns во время native turn может ответить безопасным
409; повторить после завершения. Проверку secret redaction и malformed transcript
локальные fixtures подтверждают без порчи настоящего рабочего transcript.
GitHub CI/status checks здесь не запускались; LOCAL_VERIFIED не означает CI green.

Фактический результат: PENDING SERVER VERIFICATION

## Завершение ручной проверки

После записи результатов удалить только тестовый Agent, созданный выше:

```bash
curl --fail --silent --show-error -X DELETE "$BASE_URL/agents/$AGENT_ID"
```

Не заменять фактические серверные результаты локальными тестами.
