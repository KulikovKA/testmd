# Наблюдаемость Java Development Agent

## Три представления

| Интерфейс | Назначение | Источник |
| --- | --- | --- |
| `POST /ag-ui/development-tasks/{task_id}/run` | Live-интерфейс выполнения | События DevelopmentWorkflow, преобразованные в AG-UI |
| `GET /development-tasks/{task_id}/trace` | Диагностика в Postman, восстановление экрана после disconnect | Тот же журнал событий DevelopmentTask |
| `GET /agents/{agent_id}/llm-turns?after=0&limit=10` | Безопасная диагностика завершённых Qwen turns | Проверенный native transcript и сохранённый current message |

Trace фиксирует действия приложения: успешную запись файлов, результат build,
проверенный review, полученный Git commit. Ответ модели сам по себе не доказывает
выполнение команды. `/llm-turns` помогает исследовать ответы модели, включая
внутренний phase JSON; этот JSON не является пользовательским текстом live UI.

## Контракт frontend

Разрешены стандартные [события AG-UI](https://docs.ag-ui.com/concepts/events):
`RUN_STARTED`, `STEP_STARTED`, `STEP_FINISHED`, `CUSTOM`, `TEXT_MESSAGE_START`,
`TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END`, `RUN_FINISHED`, `RUN_ERROR`.
`TOOL_CALL_*` и `REASONING_*` для workflow не применяются. Обычный Agent chat
сохраняет прежний text streaming.

Запрос development run содержит `threadId` и `runId`. Сначала приходит
`RUN_STARTED`. Каждый выполняемый шаг открывается `STEP_STARTED` и закрывается
`STEP_FINISHED` с тем же `stepName`. Имена уникальны в рамках задачи:
`requirements:1`, `planning:1`, `workspace:1`, `implementation:1`, `testing:1`,
`fixing:1`, `testing:2`, `review:1`, `committing:1`. Для повторов используется
`attempt`, включая возобновление после clarification.

`STEP_FINISHED` означает окончание шага; его успех определяется предшествующим
`CUSTOM.name=phase_status` со статусом `completed`, `failed` или `cancelled`.
Таким образом ошибка теста не превращается в зелёную отметку при начале fix-loop.

Каждый `CUSTOM.value` равен одной записи REST trace. Пример:

```json
{
  "type": "CUSTOM",
  "name": "command_finished",
  "value": {
    "sequence": 18,
    "type": "command_finished",
    "phase": "testing",
    "status": "failed",
    "step_name": "testing:1",
    "attempt": 1,
    "summary": "Команда завершена",
    "data": {
      "operation": "test",
      "command": "mvn test",
      "success": false,
      "exit_code": 1,
      "execution_backend": "agent"
    }
  }
}
```

Значения `value.type` принадлежат REST-модели UAR; корневой AG-UI `type` остаётся
`CUSTOM`. Frontend читает содержательные поля из `value.data`.

| CUSTOM.name | data | Что отображать |
| --- | --- | --- |
| `phase_status` | state при старте; status в value | Название этапа, попытку и его фактический статус |
| `llm_turn_started`, `llm_turn_finished` | turn, phase; duration_ms после завершения | Вызов Qwen без его JSON или reasoning |
| `requirements_result` | sufficient, questions_count | Достаточность требований или необходимость уточнения |
| `plan_ready` | steps | Число шагов плана |
| `files_changed` | created, modified | Отсортированные списки реально записанных файлов |
| `command_started` | operation, command | Планируемую команду test/package |
| `command_finished` | operation, command, success, exit_code, execution_backend | Фактический исход команды |
| `review_result` | approved, findings_count | Результат валидации review |
| `git_commit` | commit_id | Фактически полученный commit SHA |
| `task_status` | state; failure_code при ошибке | COMPLETED, FAILED, CANCELLED либо ожидание clarification |

Для Gradle начальная метка команды — `gradle test/build`; фактический результат
может указать `./gradlew test/build`. Связывать начало и конец по `step_name` и
`data.operation`. `exit_code=null` означает, что код неизвестен: например,
старый образ helper, test double или timeout. Успешный код не выдумывается из
boolean. `execution_backend` позволяет отличить production adapter от doubles.
Количество упавших тестов не выдаётся, если runner его не измерил.

После успешного запуска текстовые события содержат только краткую итоговую
сводку. При FAILED/CANCELLED приходит `RUN_ERROR`, без ложного успешного текста.
При WAITING_FOR_CLARIFICATION приходят сводка и `RUN_FINISHED`; состояние задачи
остаётся waiting. Вопросы читаются через существующий GET задачи, ответ
передаётся через `/clarifications`, затем выполняется новый run.

## REST trace и ограничения

Ответ `/trace`: `task_id`, `agent_id`, `state`, `events`, `summary`, `failure_code`.
Запись содержит `sequence`, `type`, `phase`, `status`, `step_name`, `attempt`,
`summary`, `data`. Sequence монотонно растёт от 1 без сброса при clarification.
Live CUSTOM использует ровно эту запись; STEP events — её транспортная проекция.
Журнал не извлекается из SSE и продолжает заполняться после disconnect.

Лимиты: 512 событий и 512 KiB сериализованных записей на задачу, 16 KiB на
событие, summary до 512 символов. Восемь записей и соответствующий запас байтов
зарезервированы для закрытия шагов и фиксации ошибки. Превышение даёт
`output_limit`; последние закрывающие события сохраняются. Очередь live остаётся
ограниченной 16 элементами и отсоединяется при disconnect/ошибке отправки headers.
Хранилище process-local; перезапуск Orchestrator теряет Agent/task/trace registry.

Отмена кооперативная: текущая операция завершается, её факт попадает в trace,
после чего новые действия не запускаются. Уже завершённый write/commit не
откатывается отменой. Повторный GET read-only; повторный run терминальной задачи
отклоняется. После reconnect сначала читать trace, не запускать задачу повторно.

## LLM turns и безопасность

Ответ содержит `agent_id`, `model`, `turns`, `next_after`. Turn: `number`, `phase`,
`prompt`, `assistant`, `duration_ms`, `input_tokens`, `output_tokens`,
`total_tokens`, `tool_names`, `prompt_truncated`, `assistant_truncated`.
Номер соответствует завершённому conversational turn Qwen, включая обычный chat
в той же сессии. Это не счётчик HTTP-запросов к LLM: внутренние model/tool
итерации не выдаются отдельными turns. Telemetry — последнее доступное значение
в native группе этого turn, без двойного суммирования копий usage. Отсутствующее
значение — `null`, а не оценка. Время CUSTOM llm_turn_finished измеряет application
turn; оно может отличаться от native duration_ms в `/llm-turns`.

Prompt берётся из сохранённого фактического current message; wrapper с прошлой
историей, system prompt и Skill context не выдаётся. Assistant — последнее
видимое текстовое сообщение native группы. Thought/reasoning parts, analysis
channels, system events, tool arguments/results, environment и произвольные
поля JSONL исключаются. Разрешены только известные имена Task tools, без payload.

Перед выходом из adapter применяется существующий redactor для настроенных
секретов и дополнительная консервативная фильтрация debug text. Текст с явными
credential assignments, authorization/cookie headers, environment dump,
certificate/private-key блоками, `.env` или reasoning markers заменяется целиком
на `[REDACTED_DEBUG_CONTENT]`. Настроенное содержимое CA тоже редактируется.
В trace и final summary нет prompt, исходников, build stdout/stderr или review
findings: выдаются безопасные факты, пути и количества.

Transcript проверяется по каноническому пути, native session UUID, JSONL-схеме,
числу завершённых turns и строгим размерам. Duplicate keys, NaN, повреждённые
структуры и незавершённые/несогласованные группы отвергаются. Стандартный лимит
файла — 8 MiB, до 20 000 записей, до 256 KiB на строку. `limit` по умолчанию 10,
максимум 20, `after` — исключённый из следующей страницы номер. Prompt/assistant
обрезаются после redaction до 8192 символов с явными флагами. Общий JSON-ответ
ограничен 256 KiB, поэтому фактическая страница может быть меньше `limit`.

| HTTP | code / смысл |
| --- | --- |
| 200 | Завершённые turns; новая сессия до первого turn возвращает пустой список |
| 409 | `llm_turns_busy`: native turn сейчас изменяет состояние |
| 404 | Agent не найден либо `llm_session_not_found` / `llm_transcript_not_found` |
| 413 | `llm_turns_limit`: превышен лимит transcript/ответа |
| 422 | Недопустимые after/limit |
| 502 | `llm_transcript_invalid`: повреждение или несогласованность; без exception details |
| 503 | `llm_turns_unavailable`: adapter не предоставляет read-only port |

Существующие `/debug-report` и `/files` сохраняют прежний контракт. Новые
endpoints наследуют текущую границу доступа API; отдельный публичный порт или
новая авторизация не добавляются. Сфера Код остаётся fail-closed skeleton.

## Проверка на сервере

Точные команды и ожидаемые результаты: проверка 10 в
[`SERVER_VERIFICATION.md`](../SERVER_VERIFICATION.md). По сообщению пользователя
baseline Java image/Kata/обычный streaming проверены на сервере. Новая
observability требует отдельного E2E; локальные doubles его не подтверждают.

## Existing repository Git facts

`repository_clone_started/finished`, `branch_created`, `repository_push_started/finished`
join the existing `git_commit` trace/CUSTOM facts. They contain only validated
repository URL, base/working branch, status, duration and safe failure codes.
Credentials, mount paths, SSH command, host-key contents and raw stderr are absent.
Clone runs before requirements; disconnect preserves owned work, cancellation waits
for in-flight helper cleanup. Failure codes include `repository_url_invalid`,
`repository_not_allowed`, `repository_auth_failed`, `repository_not_found`,
`repository_clone_failed`, `repository_branch_not_found`, `repository_push_failed`,
`repository_conflict`, `repository_unavailable`. Existing AG-UI and LLM views remain.
