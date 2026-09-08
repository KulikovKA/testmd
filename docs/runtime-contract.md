# Контракт управления AgentRuntime
Статус: реализован и проверен на детерминированном тестовом двойнике в TASK-002.
Реальное применение ограничений backend и восстановление имеют статус `NOT VERIFIED` до TASK-004/TASK-016.

## Граница и типы
`application/ports/agent_runtime.py` определяет асинхронный протокол `AgentRuntime`.
Все пять методов требуют keyword-only `options: OperationOptions`. Ни один объект SDK, состояние backend, HTTP-схема, артефакт сессии Qwen или payload диалога не пересекает эту границу.
Реализация использует только стандартную библиотеку Python.

| Метод | Вход | Успешный результат |
|---|---|---|
| `create` | `CreateRuntimeRequest` | `RuntimeObservation` |
| `start` | `RuntimeHandle` | `RuntimeObservation` |
| `status` | `RuntimeHandle` | `RuntimeObservation` |
| `stop` | `RuntimeHandle` | `RuntimeObservation` |
| `delete` | `RuntimeHandle` | `DeleteResult` |

`AgentId` и `WorkspaceId` — принадлежащие домену, валидируемые, неизменяемые идентичности, а не пути. Они принимают 1..64 ASCII-букв/цифр/дефисов/подчёркиваний и должны начинаться с буквы или цифры. `RuntimeHandle` хранит принадлежащий ему `AgentId` и непрозрачную UUID-ссылку. UUID интерпретируется только настроенным adapter в namespace развертывания этого adapter. Он не является credential или разрешением на доступ к другому Agent. Drivers проверяют владение ссылкой при каждом lookup. Backend IDs и recovery metadata остаются во владении adapter. Будущий metadata store может сохранять project reference; он никогда не должен разбирать её на backend-specific identifiers.

`runtime_values.py` владеет неизменяемыми dataclasses запросов/результатов и следующими входными значениями:

| Требование | Значение |
|---|---|
| `workload` | Валидируемый логический ключ каталога deployment, разрешаемый driver; не захардкоженные image, command или host path |
| `workspace_id` | Эксклюзивно принадлежащая записываемая storage identity; отображение mount/storage скрыто внутри driver |
| `ResourceLimits` | Явно положительное количество CPU cores и целое количество memory bytes; без неявного unlimited allocation |
| `EnvironmentVariable` | Типизированное несекретное имя/значение; значения исключаются из обычных representations |
| `SecretBinding` | Имя environment плюс непрозрачная secret reference, разрешаемая во время выполнения; значение секрета отсутствует в request |
| `NetworkDestination` | Точное DNS name/IP, port и протокол TCP/UDP; пустой tuple по умолчанию запрещает egress workload |
| `OperationOptions` | Явный конечный положительный timeout в секундах для одного вызова |

Пустые environment, secrets и network tuple являются значениями по умолчанию. Входные коллекции должны быть неизменяемыми типизированными tuples. Дублирующиеся имена environment/secret и дублирующиеся network entries отклоняются. URL syntax, wildcards, paths и scoped IPv6 values не являются network destinations. DNS resolution и необходимый infrastructure traffic находятся в ответственности driver/deployment; они не должны предоставлять неограниченный workload egress. Drivers отклоняют неподдерживаемые требования resource, network, workload или isolation с `CONFIGURATION_REJECTED`, никогда не ослабляя их молча.

Локальное создание отклоняет некорректные значения через `ValueError`, а некорректные типизированные объекты через `TypeError`, не выводя их содержимое. Это не HTTP validation layer. Будущие transport adapters всё равно валидируют входные данные до построения этих значений. Credentials должны использовать secret references/runtime injection; подавление `repr` не является encryption и не является разрешением сериализовать или логировать secrets.

## Runtime observation и жизненный цикл Agent
`ExecutionState` имеет наблюдения `INACTIVE`, `EXECUTING`, `FAULTED` и `UNKNOWN`.
Это не состояния Orchestrator `CREATING`, `STARTING`, `READY`, `BUSY`, `STOPPING`, `STOPPED` или `FAILED`. TASK-002 не реализует aggregate жизненного цикла Agent или transition service.

`Readiness.UNCONFIRMED` является значением по умолчанию, в том числе после начала execution.
`Readiness.CONFIRMED` допустим только вместе с `EXECUTING` и требует успешного настроенного interaction/readiness probe уровня агента для текущего execution.
Одного существования процесса недостаточно. Stop, failure, uncertain state и новый start аннулируют предыдущее подтверждение. Идемпотентный start уже выполняющегося instance не сбрасывает текущую readiness. Observations являются snapshots, а не readiness lease; TASK-009 отвечает за admission и rechecking policy.

Только Orchestrator решает, становится ли Agent `READY` и может ли принять turn. Lifecycle port не содержит методов chat, history, session-resume или transport. Его handle может идентифицировать execution unit для отдельного будущего interaction adapter; до TASK-005/TASK-006 никакой connection endpoint не предполагается.
Эти задачи вместе с TASK-010 отвечают за interaction/session protocol.

## Жизненный цикл и идемпотентность

| Вызов / условие | Требуемое поведение |
|---|---|
| Первый валидный `create` | Зарезервировать владение, создать один runtime/workspace, вернуть `INACTIVE` с неподтверждённой readiness; не запускать его |
| Идентичный `create` для того же Agent | Вернуть тот же handle и текущее observation без сброса execution, readiness, workspace или configuration |
| Тот же Agent с другим request | `CONFLICT`, без allocation или mutation configuration |
| Workspace уже принадлежит другому Agent | `CONFLICT`, без раскрытия handle этого Agent |
| `start` из `INACTIVE` | Обеспечить execution и вернуть observation; readiness всё ещё может быть неподтверждённой |
| `start` из `EXECUTING` | Идемпотентен; не создавать новый instance и не сбрасывать подтверждённую readiness |
| `start` из `FAULTED` / `UNKNOWN` | `INVALID_STATE`; сначала выполнить reconcile через status/stop |
| `status` | Read-only observation; невозможность определить execution даёт `UNKNOWN`, а не выдуманную readiness |
| `stop` из любого полностью созданного live allocation | Обеспечить `INACTIVE`, сбросить readiness, сохранить workspace и его содержимое; уже неактивное состояние — no-op |
| `delete` из `INACTIVE` / `FAULTED` | Безопасно остановить оставшиеся failed remnants, удалить все принадлежащие runtime ресурсы, включая workspace, затем вернуть `DeleteResult` |
| `delete` из `EXECUTING` / `UNKNOWN` | `INVALID_STATE`; перед удалением явно выполнить stop/reconcile |
| Неизвестная/удалённая reference в start/status/stop | `NOT_FOUND` |
| Неизвестная/уже удалённая reference в delete | Успешный no-op после подтверждения отсутствия в namespace настроенного adapter |
| Известная reference с другим owner | `NOT_FOUND` для каждой операции handle, включая delete; без cross-Agent mutation или раскрытия owner detail |
| Остаток partial allocation/cleanup | Status сообщает `FAULTED`; start/stop недопустимы, а повторный create возвращает `CLEANUP_FAILED` с recovery handle; delete повторяет cleanup |

`AgentId` является identity идемпотентности create на протяжении жизни Agent. Повторные вызовы используют в точности тот же типизированный request, включая порядок tuple. Drivers сериализуют конфликтующие операции для одного Agent и атомарно резервируют workspace ownership между Agents.
Конкурентные дублирующиеся вызовы create/start/stop/delete должны сходиться без дублирования ресурсов. Fake использует один per-instance lock; real adapters могут использовать более узкую сериализацию без изменения контракта. Публичная queue/reject policy для Agent turns и stop/delete во время `BUSY` остаётся в области TASK-009/TASK-010.

Успешный delete является terminal для этой identity Agent. Поздний retry create должен вернуть `CONFLICT`, никогда не воскрешая её. По-настоящему новый Agent использует новый `AgentId`.
Drivers сохраняют достаточные ownership и deletion records для этой гарантии; удаление этого состояния, пока identity всё ещё может быть повторно использована, не соответствует контракту.
Записи fake живут только столько, сколько живёт его тестовый instance. Durable driver metadata, crash recovery и фактическая persistence Orchestrator должны проверяться в соответствующих задачах реализации; этот in-memory double не заявляет такую реализацию.

Stop сохраняет непрозрачные данные workspace, которые позже могут включать session artifacts. Порт не интерпретирует и не доказывает Qwen session continuity. Delete удаляет принадлежащее runtime storage; cleanup отдельной message/session metadata относится к TASK-006/TASK-009. Не удаляйте запись Agent до успешного завершения runtime cleanup.

## Дедлайны, отмена и неопределённые результаты
Методы являются awaitable. Driver ограничивает каждый вызов через `timeout_seconds`, включая lock/admission waits и backend work. Он должен пробрасывать нативный `asyncio.CancelledError` при отмене со стороны caller. Истечение deadline преобразуется в `RuntimeFailure(TIMEOUT)`; необработанные timeout или backend exceptions не должны утекать наружу.
Budget ограничивает ожидание, но не гарантирует, что remote backend ничего не сделал.

Timeout/cancellation до mutation оставляет ресурсы неизменными. Прерывание после backend effect может оставить эффект применённым. Поэтому потерянный ответ start/stop не означает rollback. Выполните reconcile через `status` и повторите тот же handle; повторите create с тем же Agent/request, если его response был потерян. Повторите delete для подтверждения отсутствия. Никогда не отвечайте на uncertain outcome созданием нового Agent или потерей recovery references.

Ownership/recovery metadata должна быть записана до provisioning side effects.
Если create завершается ошибкой до успешного provisioning, попытайтесь выполнить bounded rollback всех partial resources. Если rollback успешен, allocation не остаётся и идентичный request может быть повторён. Если cleanup не удаётся завершить в оставшийся budget или он завершается ошибкой, сохраните handle partial allocation и workspace ownership. Обычный failed create затем поднимает `CLEANUP_FAILED` с этим handle. Cancellation остаётся `CancelledError`; timeout остаётся `TIMEOUT`. Поскольку cancellation не может вернуть handle, повторный create с тем же Agent/request должен восстановить его через результат `CLEANUP_FAILED`. Молчаливо осиротевшие ресурсы не допускаются.

После успешного provisioning неактивного instance потеря ответа create сохраняет этот завершённый instance для идемпотентного replay. Ошибка cleanup delete аналогично сохраняет recovery metadata и сообщает `CLEANUP_FAILED`; она не должна заявлять success или разрешать start оживить частично удалённые ресурсы. Повторный delete очищает только оставшиеся ресурсы. Adapters должны reconcile поздние backend effects и повторные cancellation без потери ownership; реальные remote races остаются `NOT VERIFIED`, пока driver integration tasks не проверят их.

## Ошибки и безопасная диагностика
`RuntimeFailure` предоставляет `operation`, `agent_id`, `code`, `retryable` и необязательный `handle`. Его message содержит только стабильные project values, без free-form backend cause, configuration, endpoint или secret value. Recovery handle должен принадлежать тому же Agent. Adapters преобразуют backend exceptions, подавляют raw exception chaining на этой границе и сохраняют только redacted diagnostic causes в собственных logs. Transport adapter не должен сериализовывать произвольные exception attributes или traceback locals.

| Код | Значение | Повторяемость |
|---|---|---|
| `CONFIGURATION_REJECTED` | Неподдерживаемые или отклонённые декларативные требования | Нет; исправьте configuration |
| `NOT_FOUND` | Нет доступного runtime с указанной identity | Нет |
| `INVALID_STATE` | Операция недопустима для текущего observation/cleanup state | Нет; сначала выполните reconcile/измените state |
| `CONFLICT` | Повтор identity отличается, identity завершена или есть ownership conflict | Нет |
| `UNAVAILABLE` | Runtime service/control path недоступен | Да |
| `TIMEOUT` | Budget вызова истёк, возможно после backend effect | Да |
| `CLEANUP_FAILED` | Принадлежащие ресурсы остались и требуют cleanup | Да |
| `OPERATION_FAILED` | Другая диагностированная runtime failure | Без автоматического retry |

Retryable означает reconcile и повтор той же identity, а не внутренний retry loop или разрешение выделять replacement resources. Таксономии ошибок inference, tool, conversation и HTTP намеренно отсутствуют. Readiness waiting/timeout policy относится к TASK-009; `start` не является скрытой операцией wait-until-Agent-ready.

## Переиспользуемая проверка
`tests/contract/test_agent_runtime.py` — общий behavioral suite.
`tests/runtime_support/harness.py` определяет test-only instrumentation `RuntimeHarness`; ничто из этого не является production port или selectable runtime. Fixture по умолчанию в `tests/contract/conftest.py` предоставляет `FakeRuntime` из test support.
Будущие driver tests заменяют `runtime_harness`, оставляя assertions suite неизменными. Harness предоставляет безвредный workload request, normal и interruption call budgets и ожидаемый размер inventory принадлежащих ресурсов. Он должен реализовать контролируемые failures до/allocation/commit и barriers, readiness signals, uncertain execution observations, workspace round-trips, inventory inspection и cleanup. Все hooks ограничены test-owned resources. Для real adapters используйте backend test controls/proxies и фактическую resource inspection, а не synthetic results, выдаваемые за integration evidence. Interruption budgets должны позволять достичь требуемого checkpoint до истечения времени.

Wrapper `scenario` использует новый event loop и stdlib asyncio, ограничивает каждый scenario и закрывает harness в том же loop даже после failure. Backend clients должны лениво инициализироваться в этом loop. pytest-asyncio и production dependencies не добавляются. `FakeRuntime` симулирует resource и workspace contents только в памяти; он не реализует ни network enforcement, ни inference.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contract -q
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy src tests
```

Unit checks дополнительно покрывают invalid paths/identities/budgets/network values, immutable configuration, secret-safe representations, error retry classification, dependency direction и отсутствие conversational methods в control port. Прохождение fake suite является доказательством contract/reference behavior, а не доказательством соответствия Docker или Kata.
