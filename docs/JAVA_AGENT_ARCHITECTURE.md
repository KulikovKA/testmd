# Архитектура Java Agent

## Принятый baseline и текущая архитектура

Исходная ревизия: `fc601e0f930382c2e6989f4889e01f408993540e`.
Streaming parity принята. TASK-000 / TASK-001 / TASK-002 не пересматриваются.

`composition.py` соединяет `AgentLifecycleService`, `AgentChatService`, порты и
адаптеры. `AgentRepository` хранит process-local записи Agent и историю сообщений.
`AgentRuntime` отвечает за create/start/status/stop/delete; `DockerRuntime`
создаёт один контейнер и один workspace volume на Agent и выбирает runtime
`kata` из конфигурации. Корневая ФС контейнера read-only, пользователь — 10001:10001.

`QwenSessionAdapter` сохраняет native session, transcript и состояние turn,
восстанавливает предыдущее состояние при определённых ошибках. `DockerAgentQwenRunner`
синхронизирует только adapter-owned Qwen home и Skills, сохраняя бизнес-файлы
workspace. Qwen сейчас не получает файловые/shell-инструменты; простое подключение
Skill не даёт права изменять файлы или запускать сборку.

`SkillPackageCatalog` обнаруживает `skill.json` + `SKILL.md`, валидирует пакеты,
объединяет встроенные и загруженные Skills. Выбор Skill и выдача capabilities
разделены. Существующие code-implementation, code-testing, code-review — знания
для процесса разработки, а не готовый Java execution engine.

`StreamingAgentInteraction` → bounded `TurnDeltas` → `ag_ui_events` →
`AGUIEventResponse` уже обеспечивают incremental text, heartbeat, send timeout,
detach, final consistency и commit истории только после успешного turn.

## Что переиспользуется

| Компонент | Использование в Java development |
| --- | --- |
| Agent lifecycle | Готовность, владение runtime, запрет конфликтующих операций |
| Workspace volume | Проект в `/workspace/projects/<task_id>`, изоляция от `.qwen-home` |
| Qwen session и AgentChat | Уточнение, план, предложения файлов, review/fix через обычные turns |
| Skills | Инструкции для каждой фазы, существующие формат и каталог |
| StreamingAgentInteraction / TurnDeltas / AG-UI | Текст Qwen и прогресс workflow без второго транспорта |
| Configuration / composition | Явное подключение production adapters и локальных doubles |
| Secret redaction | Общие правила очистки публичных текстов; repository credentials не передаются модели |

## Минимальные новые понятия

1. `DevelopmentTask` — задача с ТЗ, AgentId, выбранной системой сборки, optional
   repository request, вопросами, планом, ограничением fix-loop и явной state machine.
2. `DevelopmentTaskRepository` — хранение задач; первый адаптер process-local,
   как существующий Agent repository. Durable recovery не заявляется.
3. `DevelopmentTaskService` — управление переходами, уточнениями, выполнением
   контролируемых операций, отменой между завершёнными шагами и structured result.
4. `RepositoryPlatformPort` — metadata/create/get/clone information, без GitLab/
   Сфера Код DTO и без shell-команд.
5. `DevelopmentWorkspacePort` — фиксированные операции над проектом и Git,
   bounded execution/results, без универсального shell API.
6. `RepositoryCredentialPort` — только opaque reference и scoped authorization;
   секретное значение остаётся за доверенной границей.

Порядок стадий: CREATED → ANALYZING_REQUIREMENTS → (WAITING_FOR_CLARIFICATION) →
PLANNING → PREPARING_WORKSPACE → (CREATING_REPOSITORY) → IMPLEMENTING → TESTING →
REVIEWING → (FIXING → TESTING) → COMMITTING → (PUSHING) → COMPLETED.
FAILED/CANCELLED терминальны. Fix-loop ограничен; публикация требует явного
запроса пользователя и проверенного repository target.

## Repository platform и native Git

`application/ports/repository_platform.py` содержит provider-neutral values и
`RepositoryPlatformPort`: create_repository, get_repository, get_clone_information.
Metadata и clone information не содержат credentials или URL userinfo/query.
Поздний merge request добавляется отдельной операцией после появления требования.

Git clone/init/status/diff/add/commit/push остаются командами Git в доверенном
workspace adapter. Platform API не заменяет native Git. Указание branch/remote,
автора и разрешения push приходит из application policy, не из ответа модели.
Push без force; конфликт remote приводит к явной ошибке, без скрытого reset/rebase.

Первый провайдер тестов — `FakeRepositoryPlatformAdapter`. Bare remote создаётся
временным Git fixture; после push результат проверяется независимым clone/show.
Внешние Git services для локальных suites не используются.

## Workspace и выполнение Java

Production операции выполняются только внутри уже созданного Agent контейнера,
не в host orchestrator. В образ добавляются Java 21/javac, Git, Maven, Gradle,
bash/curl/unzip при сохранении Qwen и non-root runtime.

Доверенный helper принимает перечисление операций и структурированные значения:
подготовка каталога, запись проверенных файлов, сборка/тесты, Git status/diff/
commit/push. Он не принимает произвольную командную строку. Запуск ограничен
временем, объёмом вывода и фиксированным working directory. Файлы модели не могут
указывать `.git`, служебные каталоги, абсолютные пути, `..`, symlinks или устройства.

Сборка Java выполняет недоверенный код, поэтому её безопасность опирается на
существующий Kata boundary. Локальный process executor существует только в
тестах и использует временные каталоги с детерминированными fixtures. Отсутствие
Java 21/Maven/Gradle локально не меняет production architecture.

Источники расположения toolchain в официальных образах:
[Maven Docker image](https://github.com/carlossg/docker-maven),
[Gradle Docker image](https://github.com/gradle/docker-gradle).
Выбраны фиксированные версии Maven 3.9.11 и Gradle 8.14.3; совместимость
скомбинированного образа подтверждается только серверной сборкой и probe.

## Progress и существующий AG-UI

Каждая reasoning-фаза использует обычный AgentChat turn и его существующий
StreamingAgentInteraction. История Qwen фиксируется по успешным authoritative
результатам отдельных turns. DevelopmentTask имеет отдельный structured result.

Прогресс задачи использует тот же ограниченный канал `TurnDeltas`, `AcceptedTurn`,
`ag_ui_events` и `AGUIEventResponse`. С 2026-09-21 единый журнал DevelopmentTask
питает REST `/trace` и стандартные AG-UI STEP/CUSTOM events. Phase JSON Qwen
не становится пользовательским текстом. TEXT_MESSAGE_* содержит только итоговую
сводку; обычный Agent chat сохраняет прежний incremental text stream.
Disconnect отсоединяет потребителя, но не отменяет принадлежащую приложению работу.

Read-only port `AgentLLMTurnsReader` предоставляет ограниченную безопасную
проекцию native transcript через Qwen adapter. Контракты frontend, лимиты и
ошибки описаны в [DEVELOPMENT_OBSERVABILITY.md](DEVELOPMENT_OBSERVABILITY.md).

Для защиты от параллельного chat/stop/delete используется отметка владельца
development task в Agent record и проверка admission. Это расширение lifecycle,
не изменение parser/redactor/coalescer или второго streaming framework.

## Security boundaries

- Модель предлагает данные и файлы; application валидирует результат каждой фазы.
- Repository URL, namespace, branch и push authority не берутся из model output.
- В production не разрешены local/file/SSH/external-helper remotes; HTTPS-hosts
  должны быть явно разрешены deployment policy. Локальные bare paths — только
  доверенный test adapter.
- Git hooks, external diff, system/global Git config и интерактивные credential
  prompts отключены; локальная Git configuration проверяется перед операциями.
- Никаких repository credentials в Agent environment, argv, workspace, Qwen,
  transcript, AG-UI, debug-report или логах. Секретный Git credential нельзя
  безопасно передать процессу того же UID рядом с недоверенной сборкой.
- Поэтому credential boundary сначала fail-closed: authenticated transport
  требует отдельно проверенного доверенного broker. До его появления приватные
  операции отклоняются до запуска Git; public/test paths проверяются локально.
- Значения credentials не становятся domain/application DTO. Будущие секреты
  включаются в общий redaction registry до любого публичного вывода, но redaction
  не заменяет запрет доставки repository credentials модели.
- Существующая server/network policy не расширяется автоматически ради downloads,
  Maven repositories, Gradle distributions или repository provider.

## Будущая Сфера Код

`SferaCodeRepositoryAdapter` допускается только как fail-closed skeleton.
Endpoints, headers, authentication, schemas, permissions и namespace mapping
не определяются до получения настоящего контракта. Сфера Task MCP и Сфера Код —
разные интеграции. Application и domain от этого провайдера не зависят.

## Границы подтверждения

Локально: domain/application/HTTP/AG-UI tests, deterministic Qwen doubles,
workspace/path/credential policy tests, временный bare Git и Java 17 fixture,
когда соответствующие инструменты доступны.

Сервер: Java 21 образ, настоящий Kata/KVM, Qwen→Java build/review/fix→Git,
внешний LLM, сеть и будущая Сфера Код. Инструкции и незаполненные фактические
результаты — в `SERVER_VERIFICATION.md`.

## Реализованный вход и ограничения

Функция включается параметром `UAR_JAVA_DEVELOPMENT_ENABLED=true` (по умолчанию
выключена). Это добавляет Docker workspace adapter к существующему lifecycle;
второй контейнер для задачи не создаётся. Agent должен быть READY, иметь все
шесть Java Skills и пустой список tools. Reasoning остаётся в существующем Qwen
session. Скрипт helper расположен в неизменяемом образе, запускается как
`10001:10001`, проект — `/workspace/projects/<task_id>`.

| HTTP | Назначение |
| --- | --- |
| `POST /agents/{agent_id}/development-tasks` | Создать задачу с specification, build_system, branch, optional repository, publish, max_fix_attempts |
| `GET /development-tasks/{task_id}` | State, questions, plan, failure_code, structured result |
| `POST /development-tasks/{task_id}/clarifications` | Передать ответ через поле answer |
| `POST /development-tasks/{task_id}/cancel` | Запросить отмену после текущей операции |
| `POST /ag-ui/development-tasks/{task_id}/run` | Начать/продолжить задачу; тело содержит threadId и runId |

Task run использует сохранённое ТЗ. Его DTO содержит только идентификаторы потока;
существующий `/ag-ui/agents/{agent_id}/run` и его RunAgentInput не меняются.
`RUN_FINISHED` означает успешное завершение текущего запуска: задача может
ожидать clarification. Терминальное состояние проверяется через GET.
После `clarifications` нужен новый task run. До трёх раундов уточнения и до трёх
исправлений; по умолчанию два исправления. Ошибка/отмена не создаёт успешный result.

Result содержит branch, commit_id, files, checks, repository_id, published и
execution_backend. Только backend `agent` означает вызов production adapter;
сам по себе этот маркер не заменяет серверную проверку. Полный local workflow
использует настоящий Git и явно обозначенный fake Java build.

Проверенные локально ограничения: одна операция helper — 120 секунд, до 64 KiB
вывода процесса; до 64 предлагаемых файлов за turn, 32 KiB текста на файл,
256 файлов и 128 KiB текста при inventory. Контекст одного Qwen turn ограничен
16 384 символами, structured trace — 512 событиями и 512 KiB. Превышение возвращает
контролируемую ошибку; большие проекты пока требуют отдельного проектирования.
Бинарные предложения модели не поддерживаются. Существующий
`gradle/wrapper/gradle-wrapper.jar` допускается до 1 MiB, не передаётся модели и
не добавляется в Git как новое предложение. Новые проекты используют установленный
Gradle. Рабочие реестры и задача не переживают перезапуск Orchestrator.

Maven выполняет `test` и `package`, Gradle — `test` и `build`; ошибки передаются
в ограниченный fix-loop после общей redaction. После review выполняются native
status/add/staged diff/commit, а push — только при явном `publish=true`.
Пути и содержимое проверяются до записи, inventory и staged diff проверяются
на известные secrets до commit. Build environment не наследует API keys,
MAVEN_OPTS/JAVA_TOOL_OPTIONS, Git config injection или credential helpers.

Параметр `UAR_REPOSITORY_ALLOWED_HOSTS` задаёт точные HTTPS-hosts через запятую;
он не открывает сеть и не выбирает провайдера. Штатный provider сейчас skeleton
Сфера Код: repository-запрос завершится `repository_unavailable`. Реальный
путь без repository выполняет локальный Git commit внутри Agent. Fake provider
и local bare transport подключаются только явно в тестовой composition.
Authenticated clone/push остаётся закрытым до реализации доверенного broker и
получения реального контракта Сфера Код. Новые repository credentials пока
не принимаются и не передаются в Agent.
