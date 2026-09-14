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
python3 scripts/benchmark_agent.py --base-url http://127.0.0.1:8080 --scenario epic-decomposition --entity TTEST2-106 --area TTEST2 --allow-mutations --repeats 1
```

Каждый repeat создаёт, запускает, использует и удаляет свежий Agent. Клиент
использует AG-UI SSE и сохраняет TTRS, TTFT (первый `TEXT_MESSAGE_CONTENT`),
finish, общую длительность, число heartbeats, success/error и число символов
ответа. Prompts и model responses не попадают в его JSON output. Mutation
scenario отклоняется до любого HTTP request без `--allow-mutations`.
