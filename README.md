# Universal Agent Runtime — серверный запуск

## Benchmarking

Инструментация benchmark включается только через
`UAR_BENCHMARK_TIMING_ENABLED=true`. Публичный AG-UI-клиент, состав метрик,
гарантии приватности и команды описаны в [docs/BENCHMARKING.md](docs/BENCHMARKING.md).

Эта ветка содержит deployment-версию Universal Agent Runtime. Она запускает UI,
Orchestrator и изолированные Agent через Docker daemon. Qwen Code CLI находится
в `agent_image`, а inference выполняет внешний Ollama
`http://10.21.171.2:11434` с моделью `qwen3-vl-8b:latest`; локально
Ollama не устанавливается.

## Цепочка первого E2E

```text
Postman
  -> AG-UI / HTTP + SSE
    -> Orchestrator
      -> Agent
        -> Kata microVM (если выбран kata)
          -> Qwen Code
            -> restricted Sfera MCP
              -> Sfera Tasks
```

Orchestrator сохраняет владение Agent, его workspace и Qwen session. AG-UI
добавляет только transport-адаптер к существующему committed turn: Qwen сначала
формирует и фиксирует полный ответ, после чего AG-UI передаёт один
`TEXT_MESSAGE_CONTENT` с полным `delta`. Это не token-level streaming.

## Runtime

`UAR_RUNTIME_DRIVER=docker` запускает Agent как обычный Docker container.

`UAR_RUNTIME_DRIVER=kata` использует Docker как control plane и передаёт
`runtime="kata"` при создании контейнера. Один Agent соответствует одной Kata
microVM от create до delete; между сообщениями microVM не пересоздаётся.

## Sfera

При заданных `UAR_SFERA_*` Agent получает `get_task`; при дополнительно заданном
`UAR_SFERA_DEFAULT_OWNER` capability `create_task` создаёт только обычную Task,
а `create_epic` — только Epic. Skill `task-decomposition` допускает mutation
Tools только при явном текущем намерении пользователя. Перед декомпозицией он
он читает исходную сущность: создавать и привязывать дочерние задачи разрешено
только если `type` исходной сущности равен `epic`. Обычный запрос на
анализ или предложение декомпозиции не получает mutation Tools.
MCP сам выполняет `POST /app/ppau/api/auth/login`, удерживает все полученные
cookies только в памяти своего дочернего процесса и затем вызывает строго
фиксированные endpoints `GET /app/tasks/api/v1/entity-views/{entityNumber}`,
`GET /app/tasks/api/v1/entities/{entityNumber}`, `POST /app/tasks/api/v1/entities`
и узкий `PATCH /app/tasks/api/v1/entities/{parentEpic}` только для children.
При первом ответе `401` он очищает cookies, выполняет один login и повторяет
исходный запрос один раз.

Qwen не может передавать URL, HTTP method, headers или произвольное тело.
Принимаются только номера вида `TTEST2-94`; ответ нормализуется и ограничивается
по размеру. Стандартная TLS-проверка native Node HTTPS остаётся включённой.
Если сертификат Sfera подписан дополнительным корпоративным CA, задайте
`UAR_SFERA_CA_CERT_PATH` как абсолютный путь к PEM на host. При создании Agent
Orchestrator копирует PEM в его изолированный workspace; Node MCP получает путь
через `NODE_EXTRA_CA_CERTS`. Это добавляет trust anchor и не отключает системную
TLS-проверку. Если переменная не задана, Node использует только стандартный
системный trust store.
Граница capabilities: `get_task` только читает; `create_task` создаёт только
обычную Task; `create_epic` создаёт только Epic; `add_child_task` привязывает
только обычную Task к Epic. Оба creation Tools фиксируют `status=created`,
owner из deployment configuration и свой trusted type. `add_child_task` строго проверяет
существование Epic и Task через фиксированные Sfera endpoints. Если связи ещё
нет, MCP отправляет только `PATCH /app/tasks/api/v1/entities/{parentEpic}` с
payload `{"children":["childTask"]}`, затем повторно читает Epic и сообщает
успех только после появления child в `children`. Уже существующая связь не
вызывает PATCH. Не поддерживаются `update_task`, `delete_task` и
`create_subtask`.

Полный flow для нового Epic: `create_epic` → `create_task` →
`add_child_task` → `get_task` с verification children. Model не задаёт type,
owner, URL, HTTP method или headers.

`task-decomposition` остаётся независимым от Tool: с пустым списком Tools он
составляет план, а с `get_task` может дополнительно прочитать задачу.

## Development Skills

Built-in Skills are discovered from every packaged direct subdirectory of
`universal_agent_runtime.agent_assets` that contains a valid `skill.json`.
Alongside `task-decomposition`, the deployment includes knowledge-only
`decomposition-2`, `code-implementation`, `code-testing`, and `code-review`.
They are selectable together, for example:

```json
{
  "request_id": "dev-agent-unique-id",
  "skills": [
    "decomposition-2",
    "code-implementation",
    "code-testing",
    "code-review"
  ],
  "tools": []
}
```

This is a development-Skill configuration, not an autonomous coding Agent.
Filesystem, shell, Git, repository mutation, and network capabilities remain
intentionally unavailable. The planned architecture and credential/network
requirements are in [CODE_DEVELOPMENT_SCENARIO.md](docs/CODE_DEVELOPMENT_SCENARIO.md).

## Upload a Skill without restarting UAR

The Orchestrator now accepts a ZIP containing one ordinary Skill folder with
`SKILL.md`; no `skill.json` is required or allowed in the uploaded ZIP. The
deployment-owned `UAR_SKILL_REGISTRY_ROOT` is persistent; set it to an absolute
directory such as `/var/lib/universal-agent-runtime/skills`. If omitted, UAR
uses a `skills` directory beside the resolved Qwen session directory.

1. Call `GET /skills` and confirm the new ID is absent.
2. Call `POST /skills` as `multipart/form-data` with `source_type=archive`,
   `skill_id=new-skill` and `archive=<ZIP file>`; expect HTTP 201.
3. Call `GET /skills` again and confirm the ID and `source_type=archive`.
4. Create an Agent with `POST /agents` and `skills=["new-skill"]`, then start it.
5. On a turn, the package is delivered to `/workspace/.agent/skills/new-skill/`.

The same running Orchestrator handles every step; no restart or package
reinstallation is needed. The Git form is reserved and returns HTTP 501 in
this deployment. Archive format, limits, API fields, and the server smoke test
are documented in [SKILL_REGISTRY.md](docs/SKILL_REGISTRY.md). For local tests,
install `pip install -e ".[test]"`.

## AG-UI

`POST /ag-ui/agents/{agent_id}/run` принимает официальный `RunAgentInput` с
`threadId`, `runId`, `state`, `messages`, `tools`, `context` и
`forwardedProps`. Текущая реализация принимает последний plain-text user message
как новый turn уже созданного Agent; историю и capabilities владельцем остаётся
Orchestrator.

При успешном запуске SSE имеет строго такой порядок:

```text
RUN_STARTED
TEXT_MESSAGE_START
TEXT_MESSAGE_CONTENT
TEXT_MESSAGE_END
RUN_FINISHED
```

Ошибки возвращаются как `RUN_ERROR` с безопасным `code` и без диагностических
деталей. Старые REST endpoints остаются без изменений:

```text
/agents
/agents/{id}/start
/agents/{id}/messages
/agents/{id}/messages/stream
```

Готовая коллекция без secrets: [postman/Universal-Agent-Runtime.postman_collection.json](postman/Universal-Agent-Runtime.postman_collection.json).
Она использует переменные `base_url`, `agent_id`, `thread_id`, `run_id`, сохраняет
`agent_id` после создания Agent и позволяет просмотреть AG-UI и прежний SSE.

## Установка и конфигурация

Нужны Linux, Docker Engine, Python 3.11+ и сетевой доступ к Ollama и Sfera.
Сервисный пользователь должен иметь доступ к Docker.

```bash
sudo useradd --system --create-home --home-dir /var/lib/universal-agent-runtime --shell /usr/sbin/nologin uar
sudo usermod -aG docker uar
sudo install -d -o uar -g uar /opt/universal-agent-runtime /etc/universal-agent-runtime
sudo cp -a . /opt/universal-agent-runtime
sudo chown -R uar:uar /opt/universal-agent-runtime
sudo -u uar python3.11 -m venv /opt/universal-agent-runtime/.venv
sudo -u uar /opt/universal-agent-runtime/.venv/bin/python -m pip install --upgrade pip
sudo -u uar /opt/universal-agent-runtime/.venv/bin/python -m pip install /opt/universal-agent-runtime
sudo install -o root -g uar -m 0640 .env.example /etc/universal-agent-runtime/orchestrator.env
# Только если цепочка Sfera требует корпоративный CA: скопируйте PEM из
# защищённого внутреннего хранилища, не добавляя его в репозиторий.
sudo install -o root -g uar -m 0640 /secure/source/sfera-ca.pem /etc/universal-agent-runtime/sfera-ca.pem
sudoedit /etc/universal-agent-runtime/orchestrator.env
sudo -u uar /opt/universal-agent-runtime/build-agent-image.sh uar-agent:0.1.0
```

В `/etc/universal-agent-runtime/orchestrator.env` замените placeholders на
локально хранимые secrets. Не сохраняйте этот файл, `.env`, username, password,
token или cookies в Git. Для первого Kata E2E задайте:

```bash
UAR_RUNTIME_DRIVER=kata
UAR_DOCKER_NETWORK_HOST=10.21.171.2
UAR_DOCKER_NETWORK_PORT=11434
UAR_QWEN_BASE_URL=http://10.21.171.2:11434/v1
UAR_QWEN_MODEL=qwen3-vl-8b:latest
UAR_QWEN_REASONING_DIRECTIVE=/no_think
UAR_SFERA_BASE_URL=https://sfera.ai.dev.sfera-t1.ru
UAR_SFERA_USERNAME_SECRET_ID=sfera-username
UAR_SFERA_USERNAME=<secret>
UAR_SFERA_PASSWORD_SECRET_ID=sfera-password
UAR_SFERA_PASSWORD=<secret>
UAR_SFERA_DEFAULT_OWNER=<sfera-owner>
UAR_SFERA_CA_CERT_PATH=/etc/universal-agent-runtime/sfera-ca.pem
```

Остальные обязательные значения есть в `.env.example`, включая shell-safe
`UAR_DOCKER_WORKLOAD_COMMAND_JSON='["serve"]'`.

## Запуск и проверка

Запуск Orchestrator из deployment-каталога:

```bash
set -a
. /etc/universal-agent-runtime/orchestrator.env
set +a
/opt/universal-agent-runtime/run-orchestrator.sh
```

Для systemd используйте уже включённые unit-файлы только после отдельной
операционной процедуры. Локально проверяются:

```bash
curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/readyz
/opt/universal-agent-runtime/run-ui.sh
```

Для Kata найдите Agent container и проверьте, что Docker daemon назначил
runtime `kata`:

```bash
docker ps -a --filter label=io.universal-agent-runtime.managed=true
docker inspect --format '{{.HostConfig.Runtime}}' <container_id>
```

Ожидаемое значение при `UAR_RUNTIME_DRIVER=kata`: `kata`. Порты `8080` и `8090`
привязаны к loopback; UI публикуется через корпоративный reverse proxy с TLS.
