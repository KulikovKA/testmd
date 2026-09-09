# Универсальный Docker-образ агента

## Назначение и граница

`agent_image/Dockerfile` создаёт переиспользуемый образ agent runtime для
локального `DockerRuntime`. Он наследует строго закреплённый официальный Qwen
Code `0.23.1`:

```text
ghcr.io/qwenlm/qwen-code@sha256:996a12729e25f694254768ac8d3b5f870c54e6ac5825e169299e85a19c78cc10
```

Build context ограничен каталогом `agent_image/`; `.dockerignore` разрешает
только `Dockerfile` и launcher. В образ не попадают `.env`, credentials,
корпоративные endpoints, модельные веса или Ollama. Ollama остаётся внешним
deployment-owned inference service согласно ADR-0003.

Образ не является HTTP API и не заменяет `AgentInteraction`. HTTP message
schemas, lifecycle wiring `QwenSessionAdapter` и публичная сериализация turns
остаются задачами Orchestrator.

## Контракт launcher

Entry point — `/usr/local/bin/agent-runtime`, рабочая директория —
`/workspace`, пользователь — непривилегированный `10001:10001`.

| Команда | Нейтральная семантика | Результат |
| --- | --- | --- |
| `serve` | Запустить execution unit Agent | Долгоживущий процесс; readiness проверяется отдельно |
| `readiness` | Проверить готовность execution unit | Проверяет доступность Qwen Code и записываемые per-Agent каталоги; не выполняет inference request |
| `turn --session-id <UUID> --prompt <text>` | Выполнить первый native Qwen turn | Включает chat recording и создаёт native session state в workspace |
| `turn --resume <UUID> --prompt <text>` | Выполнить следующий turn той же native Qwen session | Возобновляет UUID из сохранённого workspace |
| `version` | Сообщить закреплённую версию CLI | Печатает `agent-runtime qwen-code=0.23.1` |

`turn` передаёт Qwen Code только явно настроенные endpoint/model и временный
`OPENAI_API_KEY`. Он использует headless `stream-json`, `/think`, ограниченные
лимиты и нулевой tool-call budget. Никакие Skills или Tools не включаются
неявно.

Контракт readiness нейтрален к Docker: он означает, что execution unit и его
per-Agent storage готовы к запуску Qwen. Доступность inference подтверждается
успешным `turn`, а не healthcheck.

## Конфигурация и состояние

| Значение runtime | Назначение |
| --- | --- |
| `QWEN_OLLAMA_BASE_URL` | Явный HTTP(S) endpoint с путём `/v1`, доступный из agent container |
| `QWEN_OLLAMA_MODEL` | Выбранный deployment model; в образ не включается |
| `OPENAI_API_KEY` | Значение, полученное через `SecretBinding`; не задано в Dockerfile |
| `QWEN_AGENT_MAX_WALL_TIME_SECONDS` | Wall-time одного turn, по умолчанию `300` |
| `QWEN_AGENT_MAX_SESSION_TURNS` | Лимит native Qwen session, по умолчанию `6` |

Именованный Docker workspace volume монтируется единственным записываемым
каталогом в `/workspace`. Launcher сохраняет native Qwen state в
`/workspace/.qwen-home`; поэтому native session переживает `stop`/`start` того
же `DockerRuntime` instance. Versioned manifest и project-owned history из
`QwenSessionAdapter` остаются отдельным adapter-owned механизмом TASK-006 и не
заменяются launcher.

`/workspace/.agent/skills` и `/workspace/.agent/tools` создаются как явные
per-Agent точки внедрения. TASK-007 не определяет формат, discovery или
исполняемую семантику этих каталогов: это области TASK-012/TASK-013. Они не
могут использоваться как произвольный host mount или как обход allowlist.

## Docker deployment mapping

`DockerWorkload` по умолчанию остаётся в `network_mode="none"`. Для локального
integration test workload явно выбирает `network_mode="bridge"` и задаёт
точный tuple `network_destinations`; `CreateRuntimeRequest.network` обязан
совпадать с ним. Это фиксирует intent deployment и предотвращает случайное
включение сети для других workloads.

Docker bridge сам по себе не является destination-filtering mechanism. Поэтому
этот профиль доказывает только доступность настроенного local Ollama endpoint
из финального image/runtime context; он **не** заявляет production egress
isolation. Нужный enforcement для корпоративной среды остаётся `NOT VERIFIED`
до задач Kata/network policy.

Контейнер создаётся `DockerRuntime` с read-only root filesystem, dropped
capabilities, `no-new-privileges`, PID/memory/CPU limits, без privileged mode,
host bind mounts и Docker socket. Образ создаёт `/workspace` с ownership
непривилегированного Agent; Docker заполняет новый named volume этими безопасными
каталогами до запуска процесса.

## Воспроизводимая проверка

```powershell
docker build --pull=false --tag uar-task007-agent:local agent_image
ollama pull qwen3:1.7b
$env:RUN_QWEN_OLLAMA_INTEGRATION='1'
$env:QWEN_OLLAMA_MODEL='qwen3:1.7b'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_agent_image.py -q
```

Проверка собирает образ, создаёт и запускает его через `DockerRuntime`, ждёт
healthcheck, выполняет native Qwen turn, останавливает и запускает тот же
runtime, возобновляет native UUID и проверяет случайное codeword. Она также
проверяет non-root user, read-only root filesystem, отсутствие Docker socket,
точки Skills/Tools, отсутствие endpoint/credential в image configuration и
полное удаление container/volume.
