# Universal Agent Runtime — серверный запуск

Эта ветка содержит только файлы, нужные для запуска UI, Orchestrator и
изолированных Agent в Docker. Qwen Code CLI работает в `agent_image` и
обращается к внешнему Ollama `http://10.21.171.2:11434`; локальная установка
Ollama не требуется. Модель: `qwen-3.8-multimodal:latest`.

## Требования

Нужны Linux, Docker Engine, Python 3.11+ и сетевой доступ к `10.21.171.2:11434`.
Сервисный пользователь должен иметь доступ к Docker. `task-decomposition` и
ограниченный Task MCP Tool уже включены, но Sfera Task API не активирован, пока
не задан `UAR_TASK_API_BASE_URL`.

## Установка и конфигурация

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
sudoedit /etc/universal-agent-runtime/orchestrator.env
sudo -u uar /opt/universal-agent-runtime/build-agent-image.sh uar-agent:0.1.0
```

В `orchestrator.env` замените только `UAR_QWEN_API_KEY=replace-with-secret` на
секрет, если корпоративный proxy Ollama его требует. Не сохраняйте `.env` и
секреты в Git.

## Запуск и проверка

```bash
sudo install -m 0644 universal-agent-runtime.service /etc/systemd/system/
sudo install -m 0644 universal-agent-runtime-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now universal-agent-runtime.service universal-agent-runtime-ui.service
curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/readyz
curl --fail http://127.0.0.1:8090/
```

Остановка:

```bash
sudo systemctl disable --now universal-agent-runtime-ui.service universal-agent-runtime.service
```

Порты `8080` и `8090` привязаны к loopback. Публикуйте UI через корпоративный
reverse proxy с TLS.
