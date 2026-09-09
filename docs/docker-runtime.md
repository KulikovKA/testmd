# DockerRuntime

Статус: реализован и проверен на локальном Docker Engine в TASK-004.

## Граница адаптера

`DockerRuntime` реализует application-owned порт `AgentRuntime`. Публичные методы принимают и возвращают только project-owned типы из `application/ports`. ID контейнеров, Docker SDK objects, raw Docker statuses и backend exceptions не пересекают эту границу.

Deployment передаёт адаптеру каталог `DockerWorkload`, индексированный логическими ключами `CreateRuntimeRequest.workload`. Поэтому application layer не знает image, command, container user или mount path. Image не загружается адаптером неявно: отсутствующий image приводит к `CONFIGURATION_REJECTED`.

## Отображение нейтральных требований

| Нейтральный вход | Docker mapping |
|---|---|
| `AgentId`, `WorkspaceId`, opaque reference | Project labels на container и named volume; Docker IDs остаются приватными |
| Workspace | Отдельный Docker named volume, смонтированный только в настроенный container path |
| `ResourceLimits.cpu_cores` | `NanoCpus` |
| `ResourceLimits.memory_bytes` | hard memory limit |
| `EnvironmentVariable` | Container environment |
| `SecretBinding` | Значение от injected runtime resolver; secret reference и значение не выводятся в errors |
| Пустой `network` для workload с `network_mode=none` | `network_mode=none` и `network_disabled=true` |
| Явно объявленный local-integration network profile | `DockerWorkload.network_mode="bridge"` и точное совпадение `CreateRuntimeRequest.network` с deployment-owned `network_destinations` |
| Readiness | `CONFIRMED` только для running container с Docker health status `healthy` |

Agent container создаётся с read-only root filesystem, `CapDrop=ALL`, `no-new-privileges`, PID limit, явным non-root user из `DockerWorkload`, без privileged mode, host bind mounts и Docker socket. Writable workspace предоставляется только named volume; `/tmp` — ограниченный `tmpfs`.

`bridge` profile введён TASK-007 только для воспроизводимой проверки доступа
agent image к явно настроенному local Ollama. Docker bridge не фильтрует
destination egress, поэтому это не security enforcement и не доказательство
production network isolation. Непустой network tuple без идентичного
deployment-owned profile по-прежнему отклоняется с `CONFIGURATION_REJECTED`.

## Lifecycle и восстановление

Операции сериализуются внутри экземпляра driver и ограничены `OperationOptions.timeout_seconds`. Повтор одинакового create возвращает то же allocation; изменённый request конфликтует. Успешный delete завершает identity в течение жизни driver instance. Backend вызов, выполняющийся в worker thread, доводится до наблюдаемого результата даже при async cancellation, после чего create сверяет фактическое наличие ресурсов и выполняет cleanup.

До side effects driver записывает project ownership, а каждый Docker resource получает managed labels с Agent, Workspace и opaque reference. Ошибка cleanup сохраняет recovery handle, переводит observation в `FAULTED` и требует повторного delete. Успех delete возвращается только после подтверждённого удаления container и workspace volume.

Labels позволяют оператору обнаружить ресурсы после аварийного завершения процесса. Автоматическое восстановление in-memory tombstones и records новым процессом не реализовано: durable metadata store относится к последующей persistence-задаче и имеет статус `NOT VERIFIED`.

## Проверка

`tests/contract/test_agent_runtime.py` выполняется без изменения assertions для `FakeRuntime` и реального `DockerRuntime`. Docker harness собирает отдельный безвредный image из `tests/docker_assets/Dockerfile`; это не будущий Qwen image.

`tests/integration/test_docker_runtime.py` отдельно проверяет create/start/status/stop/delete, полную очистку, environment/secret resolution, labels, named-volume workspace, CPU/RAM/PID limits и настройки изоляции. Если локальный Docker daemon недоступен, Docker cases пропускаются, но TASK-004 нельзя считать завершённой только на основании такого запуска.

`tests/integration/test_agent_image.py` — opt-in live test TASK-007. Он собирает
`agent_image/`, запускает его через `DockerRuntime` с explicit local bridge
profile, выполняет Qwen turn, проверяет native session после stop/start и
удаляет все принадлежащие runtime ресурсы.
