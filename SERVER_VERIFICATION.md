# Проверки на сервере

Сервер: **10.228.64.200**. Каталог развёртывания:
`/home/kkulikov/universal-agent-runtime-sd2`.
Orchestrator запускается вручную через `nohup`; systemd не используется.

Команды ниже — инструкции пользователю. Агент не выполнял их на сервере,
не читал и не выводил `.env`, не добавлял credentials.
Все серверные проверки: **SERVER_VERIFICATION_REQUIRED**.
Фактические результаты заполняются только после выполнения на сервере.

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
        'build_system': build, 'branch': 'main', 'publish': False,
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
    assert len(deltas) > 5
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
AG-UI дельты. При неудаче записать безопасный failure_code и состояние, не
выводить environment, Qwen native state, cookies или credentials.

Для disconnect использовать отдельную такую же задачу и оборвать `curl -N`
после первых TEXT_MESSAGE_CONTENT. Затем GET должен показать продолжение до
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

## Завершение ручной проверки

После записи результатов удалить только тестовый Agent, созданный выше:

```bash
curl --fail --silent --show-error -X DELETE "$BASE_URL/agents/$AGENT_ID"
```

Не заменять фактические серверные результаты локальными тестами.
