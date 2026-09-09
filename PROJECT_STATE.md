# Состояние проекта

Последнее обновление: 2026-09-09 после завершения TASK-005.

## Текущий статус

- TASK-000, TASK-001, TASK-002, TASK-003, TASK-004 и TASK-005 имеют статус `DONE`.
- Ни одна задача не имеет статус `ACTIVE`.
- TASK-004 реализует локальный `DockerRuntime` для runtime-neutral порта `AgentRuntime`.
- TASK-005 добавляет ограниченный исполняемый probe Qwen Code/Ollama, не добавляя conversation/session port в application layer.
- TASK-006 остаётся `TODO`; следующая задача автоматически не активирована.
- Agent Orchestrator HTTP API, persistent Qwen session adapter, restricted Task Tool и UI ещё не реализованы.

## Работающая функциональность

Проверено по TASK-004:

- `DockerRuntime` реализует `create`, `start`, `status`, `stop` и `delete` с project-owned входами, результатами и стабильными `RuntimeFailure`.
- Логический `workload` разрешается через explicit deployment-owned каталог `DockerWorkload`; Docker image, command, user и container path не попадают в application/domain.
- Каждый Agent получает отдельные container и Docker named volume; backend IDs и SDK objects остаются внутри adapter.
- CPU, memory и PID limits, environment и runtime-resolved secret bindings отображаются в Docker configuration.
- Пустой network allowlist создаёт container с `network_mode=none`; непустой allowlist отклоняется с `CONFIGURATION_REJECTED`, потому что базовый Docker не обеспечивает точный destination filtering.
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

Подробности Qwen/Ollama protocol и ограничений находятся в [docs/qwen-ollama-integration.md](docs/qwen-ollama-integration.md), Docker mapping — в [docs/docker-runtime.md](docs/docker-runtime.md), а нейтральная семантика — в [docs/runtime-contract.md](docs/runtime-contract.md).

## Текущая архитектура

`application/ports/agent_runtime.py` остаётся владельцем runtime-neutral контракта. `adapters/docker_runtime.py` зависит от этого порта и инкапсулирует Docker SDK, resource names, labels, status mapping и cleanup. Domain/application не импортируют Docker.

`DockerRuntime` не реализует conversation/session transport и не содержит Qwen-specific behavior. Каталог `DockerWorkload` является composition/deployment input, а не ветвлением use cases. Точный destination allowlist намеренно не эмулируется небезопасным unrestricted network access.

`mock_task_service` остаётся отдельным service-plane пакетом и не входит в dependency graph Universal Agent Runtime.

`qwen_ollama_probe` является standalone verification package. Он запускает официальный Qwen Code image и не реализует conversation use case, Session persistence или wiring в `DockerRuntime`; эти границы остаются за TASK-006, TASK-007 и TASK-010.

## Предположения об окружении

- Workspace: `C:\Users\Kirill\Desktop\agentt_serv`, Windows PowerShell 5.1.
- Git root: `C:\Users\Kirill\Desktop\agentt_serv`, branch `main`, remote-tracking branch `origin/main`.
- Проверки выполнены с Python 3.11.9, pytest 8.4.2, Ruff 0.16.6 и mypy 1.20.2.
- Docker Desktop context `desktop-linux` и Docker Engine 29.2.1 были доступны для реальных integration tests.
- Закреплены Docker SDK 7.2.0 и dev stubs `types-docker` 7.2.0.20260827; ранее закреплены FastAPI 0.141.1, Uvicorn 0.52.4 и HTTPX 0.28.1.
- Purpose-built test image собирается из `tests/docker_assets/Dockerfile` на закреплённом digest BusyBox; это не будущий Qwen image.
- Qwen Code `0.23.1` проверен в официальном image digest `sha256:996a12729e25f694254768ac8d3b5f870c54e6ac5825e169299e85a19c78cc10`.
- Внешний локальный Ollama `0.24.0` и модель `qwen3:0.6b` с local list ID `7df6b6e09427` были доступны для живых проверок.
- Default Docker bridge разрешал `host.docker.internal` до host Ollama. Тот же путь из окончательной managed network TASK-007 остаётся `NOT VERIFIED`.
- Корпоративные Qwen/Ollama endpoints, authentication, TLS/proxy/certificate trust и сервисы остаются `NOT VERIFIED`.

## Известные ограничения

- Автоматическое восстановление in-memory records/tombstones новым процессом не реализовано. Docker resources имеют полные ownership labels и обнаружимы; durable metadata store относится к последующей persistence-задаче и остаётся `NOT VERIFIED`.
- Непустой destination allowlist отклоняется. Без отдельного enforcement component Docker bridge не считается достаточной изоляцией egress.
- Docker health status является только backend readiness signal; Qwen interaction readiness относится к последующим задачам.
- Mock Task store остаётся single-process и in-memory; corporate schema/auth/persistence не реализованы.
- `qwen3:0.6b` успешно выполняет bounded prompt и выдаёт `read_file` tool call, но неточно следует требованию к финальному тексту после tool result.
- Native Qwen Code `--resume` и chat artifact были наблюдаемы, но 0.6B-модель не воспроизвела случайный token. Persistent conversational correctness, artifact ownership и cleanup остаются `NOT VERIFIED` до TASK-006.
- `qwen3:4b-thinking` на CPU достиг 360-second wall-time budget с exit status 55; поэтому воспроизводимый default probe использует 0.6B.
- Connectivity из final `DockerRuntime` network policy остаётся `NOT VERIFIED` до TASK-007; текущий probe использует default Docker bridge.
- Реальные Kata и корпоративные Qwen/Ollama integrations остаются `NOT VERIFIED`.
- В `TASKS.md` сохранены существовавшие пользовательские изменения форматирования и рекомендаций моделей; в TASK-005 изменён только его статус.

## Решения

Сохраняются ADR-0001 — ADR-0004. Для TASK-005 новый ADR не создан: executable probe подтверждает уже принятое ADR-0003 разделение Qwen Code и внешнего Ollama, не изменяя архитектуру.

- [ADR-0001](docs/decisions/0001-runtime-port-and-driver-boundary.md)
- [ADR-0002](docs/decisions/0002-per-agent-isolation-boundary.md)
- [ADR-0003](docs/decisions/0003-external-inference-boundary.md)
- [ADR-0004](docs/decisions/0004-runtime-retry-and-recovery-ownership.md)

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

## Последняя валидация

Дата: 2026-09-09. Результат: PASS для TASK-005.

- Qwen/Ollama unit tests: 20 passed; configuration, failure categories и `stream-json` parsing проверены.
- Live prompt probe: PASS; in-container preflight HTTP 200, точный `QWEN_OLLAMA_PROBE_OK`, Qwen session ID получен.
- Live controlled tool probe: PASS; только `read_file` наблюдался, Qwen final result event завершился успешно.
- Session/resume evidence: OBSERVED, NOT VERIFIED for persistence; тот же Qwen session ID использован, но случайный token моделью 0.6B не восстановлен.
- Container network probe: PASS для default Docker bridge через `host.docker.internal`; final managed-network assumption остаётся `NOT VERIFIED` для TASK-007.
- Full pytest suite: 207 passed.
- Ruff format check: PASS, 54 files already formatted.
- Ruff lint: PASS.
- mypy `src tests`: PASS, 40 source files.
- `git diff --check`: PASS; только предупреждения Git о platform line-ending conversion.
- Dependency boundary: PASS; standalone probe не добавил Qwen/Ollama types в domain/application.
- Все восемь acceptance criteria TASK-005 проверены; непроверенные future-runtime/corporate assumptions явно отмечены.

## Рекомендуемая следующая задача

TASK-006 — Постоянная сессия агента Qwen.

TASK-006 остаётся `TODO`; не запускать без явной команды пользователя.

Рекомендуемая модель: GPT-5.6 Sol.

Рекомендуемый режим рассуждения: High.
