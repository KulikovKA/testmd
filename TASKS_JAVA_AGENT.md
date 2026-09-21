# Задачи Java Agent

Baseline: `fc601e0f930382c2e6989f4889e01f408993540e`. Streaming принят.
Все новые Markdown-документы ведутся на русском.

| Задача | Содержание | Состояние | Проверка |
| --- | --- | --- | --- |
| Документация deployment | Фактический каталог, nohup, .env без чтения, ss | Выполнено | LOCAL_VERIFIED: проверка текста; сервер не запускался |
| TASK-JAVA-001 | Аудит и минимальная архитектура | Выполнено | LOCAL_VERIFIED: исходный код и направления зависимостей |
| TASK-JAVA-002 | Java 21 capable Agent image | Реализовано | LOCAL_VERIFIED: 2 capability tests; SERVER_VERIFICATION_REQUIRED для образа/Kata |
| TASK-JAVA-003 | DevelopmentTask domain/state machine | Выполнено | LOCAL_VERIFIED: 4 domain tests |
| TASK-JAVA-004 | Lifecycle/application service | Выполнено | LOCAL_VERIFIED: 3 service tests и 28 streaming/HTTP regression tests |
| TASK-JAVA-005 | RepositoryPlatformPort | Выполнено | LOCAL_VERIFIED: 2 contract tests |
| TASK-JAVA-006 | Fake repository adapter | Выполнено | LOCAL_VERIFIED: 3 adapter tests, без I/O к провайдерам |
| TASK-JAVA-007 | Local bare Git integration | Выполнено | LOCAL_VERIFIED: bare push/independent clone и Java 17 fixture |
| TASK-JAVA-008 | Credential boundary | Выполнено | LOCAL_VERIFIED: 4 access/secret tests; private transport fail-closed |
| TASK-JAVA-009 | Native Git workspace operations | Выполнено | LOCAL_VERIFIED: 4 Python boundary и 6 Node/Git/build/security tests |
| TASK-JAVA-010 | Skills Java development | Выполнено | LOCAL_VERIFIED: 22 catalog/manifest/authorization tests |
| TASK-JAVA-011 | Controlled Java execution | Реализовано | LOCAL_VERIFIED: 6 Node tests; LOCAL_NOT_AVAILABLE: настоящий Maven/Gradle |
| TASK-JAVA-012 | Workflow, HTTP и существующий AG-UI | Реализовано | LOCAL_VERIFIED: 9 workflow/HTTP/native Git tests; SERVER_VERIFICATION_REQUIRED для настоящего Qwen/Java/Kata |
| TASK-JAVA-OBS-001 | Единый bounded trace и STEP/CUSTOM development stream | Реализовано | LOCAL_VERIFIED: normal/failure/fix/cancel/disconnect, последовательность, quotas, совпадение REST/live |
| TASK-JAVA-OBS-002 | Безопасный read-only LLM turns port и endpoint | Реализовано | LOCAL_VERIFIED: transcript/telemetry/order/redaction, malformed/missing/limits; SERVER_VERIFICATION_REQUIRED для live Qwen |
| TASK-JAVA-OBS-003 | Postman и frontend/server contract | Реализовано | LOCAL_VERIFIED: JSON коллекции; server procedure 10 подготовлена, не выполнена |

После каждой задачи обновляются этот файл и `PROJECT_STATE_JAVA_AGENT.md`,
выполняются применимые локальные tests и `git diff --check`.
Реальные серверные проверки не подменяются результатами doubles.

Итог на 2026-09-20: **111 Python tests и 74 Node tests прошли**.
Ruff для всех изменённых Python-файлов, shell syntax `agent-runtime` и
`git diff --check` прошли. Новые commits/push не выполнялись.
Реальная Сфера Код и authenticated Git остаются намеренно незавершёнными
интеграциями до получения контракта и проверки доверенного credential broker.

Observability, 2026-09-21: baseline `3d5cabe` уже закоммичен и отправлен ранее.
Прошли **51 targeted**, **126 Python tests** полного набора и **74 Node tests**.
Серверный `.dockerignore` fix не перезаписывался; локальный файл не изменялся.
Новая observability пока не закоммичена; GitHub CI и server E2E не запускались.
