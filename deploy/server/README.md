# Серверная поставка Universal Agent Runtime

Этот каталог собирает отдельную минимальную поставку. Исходное R&D-дерево не
изменяется и не переносится на сервер целиком. В итоговую поставку входят только
`universal_agent_runtime`, `agent_image`, UI, конфигурационный шаблон и скрипты
запуска.

В поставку не входят `AGENTS.md`, `MODEL_GUIDE.md`, `PROJECT_STATE.md`,
`TASKS.md`, `tests/`, `docs/`, `mock_task_service`, `qwen_ollama_probe`, кэши,
`.venv` и файлы IDE. Сборщик не копирует секреты: файл `.env` создаётся на
сервере из `.env.example` и имеет права, доступные только сервисному пользователю.

`task-decomposition/SKILL.md`, `skill.json` и
`task_rest_mcp_server.mjs` входят в Python package data. MCP-инфраструктура
остаётся доступной для следующего этапа подключения Sfera Task API, но пока
`UAR_TASK_API_BASE_URL` не задан, Task Tool не предоставляется агентам.

## Сборка поставки

В корне исходного репозитория выполните:

```bash
chmod +x deploy/server/build-bundle.sh
deploy/server/build-bundle.sh
tar -C dist/server -czf universal-agent-runtime-server.tar.gz .
```

Получившийся `dist/server` — единственный каталог, который нужно перенести на
Linux-сервер. Передавайте архив по защищённому корпоративному каналу.

## Установка на Linux-сервере

На сервере должны быть Docker Engine с доступом сервисного пользователя к Docker,
Python 3.11+ и сетевой доступ к внешнему Ollama. Добавьте пользователя `uar` в
группу Docker по правилам вашей ОС и перезапустите его сессию. Ollama и модель локально не
устанавливаются; в `.env` указывается внешний endpoint и модель
`qwen-3.8-multimodal:latest`.

```bash
sudo useradd --system --create-home --home-dir /var/lib/universal-agent-runtime --shell /usr/sbin/nologin uar
sudo usermod -aG docker uar
sudo install -d -o uar -g uar /opt/universal-agent-runtime /var/lib/universal-agent-runtime /etc/universal-agent-runtime
sudo tar -xzf universal-agent-runtime-server.tar.gz -C /opt/universal-agent-runtime
sudo chown -R uar:uar /opt/universal-agent-runtime
sudo -u uar python3.11 -m venv /opt/universal-agent-runtime/.venv
sudo -u uar /opt/universal-agent-runtime/.venv/bin/python -m pip install --upgrade pip
sudo -u uar /opt/universal-agent-runtime/.venv/bin/python -m pip install /opt/universal-agent-runtime
sudo install -o root -g uar -m 0640 /opt/universal-agent-runtime/.env.example /etc/universal-agent-runtime/orchestrator.env
sudoedit /etc/universal-agent-runtime/orchestrator.env
sudo -u uar /opt/universal-agent-runtime/build-agent-image.sh uar-agent:0.1.0
```

Перед запуском замените `ollama.corp.example` и `replace-with-secret` в
`/etc/universal-agent-runtime/orchestrator.env` на корпоративные значения.
Укажите имя образа из команды сборки в `UAR_DOCKER_WORKLOAD_IMAGE`. Не задавайте
`UAR_TASK_API_BASE_URL` до согласованного подключения Sfera Task API.

## Запуск

Для управляемого запуска установите unit-файлы:

```bash
sudo install -m 0644 /opt/universal-agent-runtime/universal-agent-runtime.service /etc/systemd/system/
sudo install -m 0644 /opt/universal-agent-runtime/universal-agent-runtime-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now universal-agent-runtime.service universal-agent-runtime-ui.service
curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/readyz
curl --fail http://127.0.0.1:8090/
```

Для диагностики без systemd используйте два отдельных терминала после загрузки
переменных из `/etc/universal-agent-runtime/orchestrator.env`:

```bash
set -a; . /etc/universal-agent-runtime/orchestrator.env; set +a
/opt/universal-agent-runtime/run-orchestrator.sh
```

```bash
set -a; . /etc/universal-agent-runtime/orchestrator.env; set +a
/opt/universal-agent-runtime/run-ui.sh
```

Опубликуйте UI и Orchestrator через корпоративный reverse proxy с TLS. Порты
`8080` и `8090` в шаблоне привязаны к loopback и не предназначены для прямого
внешнего доступа.
