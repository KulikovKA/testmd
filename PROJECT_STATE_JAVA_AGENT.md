# Состояние Java Agent

Дата: 2026-09-21. Baseline Java workflow:
`3d5cabe5325b57b2160e4e6fb52b74e7e9a3b472` (ранее закоммичен и отправлен).
Принятый streaming baseline: `fc601e0f930382c2e6989f4889e01f408993540e`.

## Observability

Реализован единый bounded trace DevelopmentTask. Development AG-UI выдаёт
официальные STEP/CUSTOM и финальную сводку; phase JSON не попадает в текстовый
поток. `/trace` возвращает тот же журнал, включая errors/fix/cancel и реальные
file/build/commit outcomes. Обычный chat, debug-report и files совместимы.
Read-only port `/llm-turns` предоставляет безопасные завершённые Qwen turns,
telemetry и pagination. Transcript считается недоверенным, hidden reasoning,
system prompt и raw tool/environment данные исключаются до HTTP boundary.
Контракты: `docs/DEVELOPMENT_OBSERVABILITY.md`; Postman обновлён.

LOCAL_VERIFIED: 51 targeted tests, полный набор **126 Python tests** и
**74 Node tests**. Проверен реальный exit code bounded runner и совместимость
со старым helper (неизвестный exit_code=null). Сборка образа локально не запускалась.

По сообщению пользователя baseline Java image/Kata/READY/Qwen/обычный streaming
уже проверены на сервере. Новая observability остаётся
SERVER_VERIFICATION_REQUIRED; точные команды — проверка 10 SERVER_VERIFICATION.md.
Серверный намеренный `.dockerignore` diff находится только на сервере; локальный
файл этой задачей не менялся. Сфера Код и credentials integration не изменялись.
GitHub CI/status checks не запускались.

## Выполнено

- Исправлен `SERVER_VERIFICATION.md`: реальный каталог
  `/home/kkulikov/universal-agent-runtime-sd2`, ручной nohup, поиск PID через ss.
- Завершён TASK-JAVA-001. Архитектура и security boundaries зафиксированы в
  `docs/JAVA_AGENT_ARCHITECTURE.md`.
- Найдены точки повторного использования: lifecycle, AgentChat/Qwen session,
  workspace volume, Skills, bounded TurnDeltas, существующий AG-UI.

## Текущая работа

TASK-JAVA-002 реализован: определение образа с Java 21, Maven 3.9.11,
Gradle 8.14.3 и остальными утилитами, non-root пользователь сохранён.
Два capability tests прошли локально; реальная сборка образа не выполнялась.
TASK-JAVA-003 реализован: явные переходы, вопросы, лимит исправлений и terminal
states; 4 domain tests прошли. TASK-JAVA-004 реализован: создание, admission,
владение Agent, clarification, cooperative cancellation, optimistic versions.
35 связанных domain/service/AG-UI тестов прошли; streaming internals не менялись.
TASK-JAVA-005 выполнен: metadata/create/clone contracts без provider DTO и
credentials в URL; 2 contract tests прошли. TASK-JAVA-006 выполнен: fake adapter
с идемпотентностью и fail-closed skeleton Сфера Код; 3 adapter tests прошли.
TASK-JAVA-007 выполнен: настоящий local bare Git push независимо проверен clone;
детерминированный Java fixture скомпилирован и запущен доступным JDK 17.
Два integration tests прошли. Это не Java 21/Kata E2E.
TASK-JAVA-008 выполнен: opaque credential reference, host/port/transport policy,
отдельное разрешение push, common redaction и запрет secret-bearing файлов.
Четыре security tests прошли; private transport закрыт до проверенного broker.
TASK-JAVA-009 выполнен: закрытый workspace port, Docker adapter, native Git helper,
проверка конфигурации Git, путей и лимитов исполнения. Прошли 3 Python boundary
tests и 5 Node/native Git/security tests. TASK-JAVA-010 выполнен: шесть Skills
используют существующий каталог, 22 catalog/manifest/authorization tests прошли.
TASK-JAVA-011 реализован: закрытые Maven/Gradle команды, очищенное окружение,
лимиты времени/вывода; 6 Node tests прошли. Реальный build ожидает сервер.
TASK-JAVA-012 реализован: HTTP create/get/clarification/cancel, reasoning через
существующие Qwen turns, bounded progress через существующий AG-UI, build/review
fix-loop, native commit и явно разрешённый push, structured result.
Девять workflow/HTTP/native Git tests прошли. Проверены продолжение после
disconnect, отмена после текущей операции, сохранение native turn count/history,
запрет параллельных chat/stop/delete, ошибки модели и исчерпание fix budget.
Настоящий local bare push независимо проверен через Git HEAD и содержимое файла.
Java build в этом workflow-тесте явно обозначен как fake.

## Проверки

- `LOCAL_VERIFIED`: Git 2.53.0, Java/javac 17.0.18, Python 3.12.14, Node 24.19.0.
- `LOCAL_VERIFIED`: baseline ранее прошёл 80 Python и 66 Node tests.
- `LOCAL_VERIFIED`: после Java Agent изменений прошли все **111 Python tests**
  и **74 Node tests**. Изменённый workspace helper после последней правки
  повторно прошёл свои 6 Node tests. Пройдены Ruff для изменённых Python-файлов,
  shell syntax и `git diff --check`.
- `LOCAL_NOT_AVAILABLE`: Java 21, Maven и Gradle в PATH отсутствуют.
- `SERVER_VERIFICATION_REQUIRED`: Docker/Kata/KVM, production image, серверный
  Qwen, внешний LLM, сеть, настоящая Сфера Код.

Системное ПО не устанавливается. `.env` не читается. Команды на сервере не
выполняются. Repository credentials/API-контракт Сфера Код не запрашивались:
без них можно реализовать ports, fail-closed boundary и deterministic tests.

## Ограничения

Локальная реализация TASK-JAVA-001–012 завершена. Production feature по умолчанию
выключена; включение — `UAR_JAVA_DEVELOPMENT_ENABLED=true`. Agent использует все
шесть Java Skills и пустой список tools. Схема HTTP, лимиты файлов/контекста и
execution backend описаны в `docs/JAVA_AGENT_ARCHITECTURE.md`.
Сборка настоящего образа и Qwen→Java 21→test/build→review→Git на Kata ожидают
выполнения процедур 7–8 в `SERVER_VERIFICATION.md`. Серверные результаты не
заполнены, server E2E не заявляется.
Реестры Agent/задач остаются process-local, durable recovery не заявляется.
Private Git transport до проверенного broker отклоняется до запуска Git.
Штатный provider Сфера Код — fail-closed skeleton. Без repository доступен
build/test/commit в Agent; реальная публикация требует будущего provider adapter.
Новый allowlist repository hosts не изменяет существующую server network policy.
Новые изменения не закоммичены; коммит/push не входят в текущее поручение.
