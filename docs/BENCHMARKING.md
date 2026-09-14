# Benchmarking

Значение `UAR_BENCHMARK_TIMING_ENABLED=false` является значением deployment по
умолчанию. При нём Orchestrator и ограниченный Sfera MCP не формируют подробные
benchmark-записи. Устанавливайте `true` только для измеряемого запуска. Флаг
включает монотонные метрики длительностей в логе Orchestrator и записи
`UAR_METRIC {json}` в stderr MCP. Метрики не содержат prompts, model responses,
номера сущностей, request bodies, URLs, cookies, credentials или API keys.

Граница измерения — один committed turn Agent. `agent_interaction` покрывает
вызов interaction port, а `agent_turn` — принадлежащий приложению turn вместе с
валидацией и commit. `qwen_execution` измеряет запуск Qwen CLI внутри уже
созданного lifecycle-owned Agent container. Контейнер не создаётся на каждый
turn, поэтому отдельная метрика container start для этого пути недоступна.
`qwen_execution_ms` относится к самому вызову CLI, а `qwen_total_ms` включает
весь Qwen runner boundary, включая подготовку и возврат session state.
Количество запросов Ollama inference возвращается как `null` вместе с
`backend_inference_requests_observable=false`: deployment не наблюдает отдельные
inference requests и не делает предположений об их числе.

MCP пишет индивидуальные записи `mcp_tool` и `sfera_http`. Sfera routes
классифицируются только как `login`, `entity_view_get`, `entity_get`,
`entity_create` или `entity_patch`; фактические entity number и URL не выводятся.
Повторные HTTP requests после 401 видны как отдельные записи.

Запускайте клиент через public API после готовности Orchestrator:

```bash
python3 scripts/benchmark_agent.py --base-url http://127.0.0.1:8080 --scenario chat --repeats 5 --output benchmark-chat.json
python3 scripts/benchmark_agent.py --base-url http://127.0.0.1:8080 --scenario read --entity TTEST2-106 --repeats 5
python3 scripts/benchmark_agent.py \
  --base-url http://127.0.0.1:8080 \
  --scenario epic-decomposition \
  --area TTEST2 \
  --allow-mutations \
  --repeats 3 \
  --output /tmp/uar-baseline.json
```

Каждый repeat создаёт, запускает, использует и удаляет свежий Agent. Для
`epic-decomposition` клиент просит создать новый Epic в указанной area для
AI-агента проверки технических требований и декомпозировать его на задачи; он
не передаёт исходную entity, число задач или последовательность Tools.

Клиент сохраняет безопасные `agent_id`, `thread_id` и `run_id`, но не сохраняет
prompt или model response. `agent_start_ms` измеряется строго вокруг start
endpoint. Отдельный AG-UI timer запускается непосредственно перед POST run;
от него измеряются `time_to_run_started_ms`, `time_to_first_text_ms` и
`time_to_run_finished_ms`. `ag_ui_total_ms` покрывает весь AG-UI request.
`iteration_total_ms` начинается после успешного POST `/agents` и заканчивается
после AG-UI run; DELETE cleanup в него не входит. Результат каждого run также
содержит `cleanup_http_status`. Ошибка cleanup, включая 409 для BUSY/FAILED
Agent, не скрывает основной AG-UI результат и не форсирует lifecycle delete.

В terminal выводится компактная таблица repeat и aggregates min / median / mean
/ p95 / max. JSON остаётся machine-readable: без `--output` он пишется в stdout,
а таблица — в stderr; с `--output` JSON записывается в файл. Mutation scenario
отклоняется до любого HTTP request без `--allow-mutations`.

Application metrics содержат `agent_id` и application `turn_id`. Qwen, MCP и
Sfera metrics содержат `agent_id` и безопасный turn correlation вида
`native_session_id:turn_ordinal`. AG-UI `run_id` не передаётся через domain.
