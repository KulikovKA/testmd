# Тестовый Task REST API

TASK-003 предоставляет автономный process-local сервис для контролируемых тестов Tools.
Он моделирует только получение задач, создание задач, создание подзадач и обновление. Его schema
является синтетической и не подразумевает никакую корпоративную schema, authentication, database
или production-поведение. Каждый вызов `create_app()` владеет изолированным пустым store.

## Конфигурация и запуск

Потребители настраивают базовый endpoint через `MOCK_TASK_API_BASE_URL`; сам сервис
не считывает это значение. Bind address и port задаются через
`MOCK_TASK_API_HOST` и `MOCK_TASK_API_PORT`. Безопасные значения по умолчанию находятся в `.env.example`.
Никакой `.env` loader или настройки credentials не предусмотрено.

```powershell
$env:MOCK_TASK_API_HOST = "127.0.0.1"
$env:MOCK_TASK_API_PORT = "8001"
$env:MOCK_TASK_API_BASE_URL = "http://127.0.0.1:8001"
.\.venv\Scripts\python.exe -m mock_task_service
```

Состояние существует только на протяжении жизни этого process. Перезапуск очищает его. Тесты создают собственные
app и store, поэтому тесты никогда не используют общие записи задач.

## Ресурс и операции

Ответ Task содержит `id`, `title`, `description`, `status`, `parent_id`,
`subtask_ids` и целочисленный `version`. Status принимает одно из значений `open`, `in_progress`,
`done` или `cancelled`. IDs выделяются последовательно как `task-0001`,
`task-0002` и так далее в пределах одного store.

| Поведение Tool                                                                          | HTTP-операция                    | Успешный результат  |
| --------------------------------------------------------------------------------------- | -------------------------------- | ------------------- |
| `get_task`                                                                              | `GET /tasks/{task_id}`           | `200` Task          |
| `create_task`                                                                           | `POST /tasks`                    | `201` Task          |
| `create_subtask`                                                                        | `POST /tasks/{task_id}/subtasks` | `201` дочерний Task |
| `update_task`                                                                           | `PATCH /tasks/{task_id}`         | `200` Task          |
| Тело create требует непустой `title` (максимум 200 символов) и допускает                |                                  |                     |
| необязательный `description` (максимум 4000 символов). Подзадача сохраняет `parent_id`, |                                  |                     |
| а её ID атомарно добавляется в `subtask_ids` родителя; version родителя                 |                                  |                     |
| увеличивается. Вложенные подзадачи разрешены по тому же правилу.                        |                                  |                     |
| Тело update требует `expected_version` и как минимум одно из полей `title`,             |                                  |                     |
| `description` или `status`. Успешное обновление увеличивает version. Устаревшая         |                                  |                     |
| version возвращает `409 version_conflict` и не изменяет запись.                         |                                  |                     |

```powershell
Invoke-RestMethod -Method Post -Uri "$env:MOCK_TASK_API_BASE_URL/tasks" `
-ContentType "application/json" -Body '{"title":"Synthetic parent","description":"Local test only"}'
Invoke-RestMethod -Method Post -Uri "$env:MOCK_TASK_API_BASE_URL/tasks/task-0001/subtasks" `
-ContentType "application/json" -Body '{"title":"Synthetic child"}'
Invoke-RestMethod -Method Get -Uri "$env:MOCK_TASK_API_BASE_URL/tasks/task-0001"
Invoke-RestMethod -Method Patch -Uri "$env:MOCK_TASK_API_BASE_URL/tasks/task-0001" `
-ContentType "application/json" -Body '{"expected_version":2,"status":"in_progress"}'
```

## Ошибки и ограничения

Ошибки используют формат `{"error":{"code":"...","message":"..."}}`. Отсутствующие задачи возвращают
`404 task_not_found`; устаревшие версии возвращают `409 version_conflict`; некорректные paths,
bodies, enum values, отсутствующие fields, лишние fields и нарушения ограничений размера возвращают
редактированный `422 invalid_request`. Ответы с ошибками не повторяют некорректный input.
Этот mock намеренно является single-process и in-memory. Его lock делает операции в одном process
детерминированными, однако несколько workers имели бы независимое состояние и
не должны использоваться. Отсутствуют list/delete operation, persistence, authentication,
external database, corporate endpoint/data, импорт Orchestrator/runtime и
поведение декомпозиции задач. Будущий restricted Tool adapter относится к области TASK-012.
