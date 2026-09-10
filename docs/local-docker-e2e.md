# Local Docker end-to-end validation

TASK-014 provides one opt-in scenario that crosses the public Orchestrator HTTP
surface, the lifecycle-owned Docker containers, Qwen Code, an external Ollama
model, and the mock Task HTTP service. The test starts real TCP Uvicorn servers
for the Orchestrator and mock service, while Ollama remains a host service.

## Prerequisites and command

Run from the repository root with Docker Desktop and Ollama available. The
validated local profile uses `qwen3:1.7b` and the explicit `/no_think` directive
to keep the CPU-only scenario bounded while retaining native tool calling.

```powershell
ollama pull qwen3:1.7b
$env:RUN_LOCAL_DOCKER_E2E='1'
$env:QWEN_OLLAMA_MODEL='qwen3:1.7b'
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_local_docker_task_decomposition.py -q --junitxml=.pytest_cache/task014-e2e.xml
```

`QWEN_OLLAMA_BASE_URL` and `QWEN_OLLAMA_API_KEY` may override the local safe
defaults. The test never writes their values to assertions or diagnostic
artifacts. It builds `uar-task007-agent:local` without pulling a replacement
base image.

## Evidence produced by the scenario

The scenario performs the following checks:

1. It creates two Agents only through public Orchestrator HTTP endpoints, starts
   them, and waits for `READY`.
2. The first Agent proposes a hierarchy, receives changed context through the
   committed-content SSE endpoint, and returns the revised hierarchy. Public
   Task reads prove that no record exists before explicit confirmation.
3. Two confirmed turns invoke only `create_task` and `create_subtask`. The MCP
   bridge validates and journals successful mutation results for that turn; the
   Qwen adapter validates the journal again and appends the exact records to the
   public answer. Identical mutation retries inside one turn reuse the first
   result and do not issue another REST request.
4. Public Task reads verify the parent/child titles and relationship. The test
   checks JSON chat and the TASK-011 committed-content SSE sequence.
5. Distinct Agent, Workspace, Session, container, and volume identities prove
   isolation. A stop/start cycle preserves the first Session marker, while a
   turn in the second Session cannot observe it.
6. Public stop/delete calls remove both Agents. Final Docker label queries and
   adapter storage checks prove that no owned container, volume, or Session
   directory remains.

The agent image contains Qwen Code and the restricted MCP process. It does not
contain Ollama, model weights, or the Docker socket. The configured bridge
endpoint is required for the container to reach the external Ollama service.

## Timeouts, failures, and cleanup

Server startup, readiness polling, HTTP requests, Task REST calls, SSE sends,
Qwen execution, and server shutdown all have finite limits. Normal assertion or
interaction failures enter `finally`, stop/delete every created Agent through
the public API, and then close both Uvicorn servers.

The JUnit artifact contains test status and synthetic assertion data. It must
not be used with prompts or fixtures containing credentials or production data.
On a normal failure, inspect the redacted HTTP error, the JUnit failure, and the
managed-resource inventory before retrying:

```powershell
docker ps -a --filter label=io.universal-agent-runtime.managed=true
docker volume ls --filter label=io.universal-agent-runtime.managed=true
```

If the pytest process itself was forcibly terminated, `finally` may not run.
On an isolated local test host, inspect the two inventories above, remove only
the exact test-owned names they report, confirm that both inventories are empty,
and then rerun the full command. Never use this manual cleanup procedure on a
host that contains Agents another process still owns.

`qwen3:0.6b` was fast but did not reliably emit native tool calls for this
multi-turn hierarchy. Thinking mode on the tested CPU exceeded a practical
mutation-turn budget. These failed attempts did not satisfy TASK-014; the PASS
evidence is the `qwen3:1.7b` `/no_think` run.
