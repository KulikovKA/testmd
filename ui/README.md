# UI

UI предоставляет браузеру доступ к существующему Universal Agent Runtime
Orchestrator.

## Схема взаимодействия

```text
Браузер
  -> прокси UI :8090
    -> Orchestrator :8080
      -> Docker Runtime
        -> Qwen Code
          -> внешний Ollama
          -> ограниченный read-only Sfera Task Tool
```

UI не подключается к Ollama напрямую и не обходит жизненный цикл Agent, Skill
или авторизацию Tool.

## Возможности

- проверка health/readiness Orchestrator;
- создание Agent;
- выбор `task-decomposition`;
- выбор `get_task` для чтения задачи Sfera;
- запуск, остановка, просмотр и удаление Agent;
- постоянный JSON-чат;
- загрузка history;
- повторное открытие последнего Agent через browser localStorage.

SSE в этой версии не используется: Orchestrator возвращает подтверждённое
содержимое, а не token-by-token streaming. JSON-чат проще и сохраняет ту же
серверную семантику.

## Запуск

Сначала должен работать Orchestrator на `127.0.0.1:8080`.

PowerShell:

```powershell
$env:UAR_UI_ORCHESTRATOR_BASE_URL='http://127.0.0.1:8080'
$env:UAR_UI_HOST='127.0.0.1'
$env:UAR_UI_PORT='8090'
python .\ui\server.py
```

Откройте:

```text
http://127.0.0.1:8090
```

Linux:

```bash
export UAR_UI_ORCHESTRATOR_BASE_URL=http://127.0.0.1:8080
export UAR_UI_HOST=127.0.0.1
export UAR_UI_PORT=8090
python ui/server.py
```

Не сохраняйте корпоративные endpoints и credentials в репозитории. Они остаются
deployment-конфигурацией Orchestrator.
