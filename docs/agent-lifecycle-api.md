# Agent lifecycle API

## Scope

TASK-009 adds runtime-neutral Agent lifecycle use cases and exposes them through
the HTTP adapter established by TASK-008. TASK-010 extends it with
[JSON chat/history](agent-chat-api.md); TASK-011 adds
[turn-bound SSE](agent-streaming-api.md) with the same lifecycle policy.
Task-specific behavior, Skill loading, and Tool authorization remain outside this API task.

The application service calls only `AgentRuntime`, `AgentInteraction`, and the
project-owned `AgentRepository` port. Docker SDK objects, Qwen-native session
identifiers, secret values, and backend resource identifiers never enter the
domain, application responses, or HTTP schemas.

## HTTP contract

| Method and path | Meaning | Success |
| --- | --- | --- |
| `POST /agents` | Provision one stopped Agent and initialize its logical Session | `201`, or `200` for an identical idempotent replay |
| `GET /agents/{agent_id}` | Read the current Orchestrator-owned record | `200` |
| `POST /agents/{agent_id}/start` | Start the existing runtime and wait for confirmed Agent readiness | `200` |
| `POST /agents/{agent_id}/stop` | Stop execution while retaining workspace and Session | `200` |
| `DELETE /agents/{agent_id}` | Delete runtime resources and Session artifacts | `204` |

`POST /agents` deliberately performs create without start. A successful create
therefore returns `STOPPED`; start and readiness remain an explicit operation.
Stop is distinct from delete so that a later start uses the same runtime,
workspace, and logical Session identity.

The create body is:

```json
{
  "request_id": "client-request-001",
  "skills": [],
  "tools": []
}
```

`request_id` is a required 1..64 character idempotency identity. Replaying the
same request ID with the same capability references returns the existing Agent.
Reusing it with different references returns `409 conflict`. Skill and Tool IDs
are only immutable configuration references in TASK-009; their packaging,
loading, authorization, and behavior remain TASK-012/TASK-013.

A successful representation has this safe shape:

```json
{
  "agent_id": "agent-<opaque-id>",
  "workspace_id": "workspace-<opaque-id>",
  "session_id": "session-<opaque-id>",
  "state": "STOPPED",
  "configuration": {
    "workload": "qwen-agent-image",
    "cpu_cores": 1.0,
    "memory_bytes": 1073741824,
    "skills": [],
    "tools": []
  },
  "runtime": {
    "execution": "inactive",
    "readiness": "unconfirmed"
  },
  "failure": null
}
```

Runtime handles, Docker names/IDs, host paths, endpoint values, secret
references/values, and Qwen-native IDs are not serialized.

## Lifecycle and readiness

The Orchestrator owns `CREATING`, `STARTING`, `READY`, `BUSY`, `STOPPING`, `STOPPED`,
and `FAILED`. Runtime observations remain the separate `ExecutionState` and
`Readiness` values owned by the `AgentRuntime` port.

```text
POST /agents:               CREATING -> STOPPED | FAILED
POST /agents/{id}/start:    STOPPED -> STARTING -> READY | FAILED
POST /agents/{id}/stop:     READY|FAILED -> STOPPING -> STOPPED | FAILED
DELETE /agents/{id}:        STOPPED|FAILED -> removed | FAILED
```

Start returns `READY` only when the runtime reports both `executing` and
`confirmed`. The Docker composition installs the universal image readiness
command as its health check. Polling, per-call operation timeouts, the overall
readiness timeout, and poll interval are explicit deployment configuration.
A readiness timeout retains the runtime handle and Agent record in `FAILED`;
the caller can inspect it, stop it, and retry cleanup without allocating a
replacement identity.

Conflicting lifecycle operations for one Agent are serialized. Idempotent
duplicate create/start/stop/delete operations converge on the same identity and
do not allocate replacement runtime resources. Different Agents retain distinct
Agent, workspace, Session, and runtime identities.

## Errors and cleanup

All expected failures use the redacted envelope from TASK-008. Lifecycle errors
may add only safe recovery fields:

```json
{
  "error": {
    "code": "readiness_timeout",
    "message": "Agent readiness timed out",
    "operation": "start",
    "agent_id": "agent-<opaque-id>",
    "state": "FAILED",
    "retryable": true
  }
}
```

| HTTP | Stable categories |
| --- | --- |
| `404` | `not_found` |
| `409` | `invalid_state`, `conflict` |
| `422` | `request_invalid`, `configuration_rejected` |
| `502` | `runtime_not_found`, `session_failed`, `operation_failed` |
| `503` | `runtime_unavailable`, `cleanup_failed` |
| `504` | `timeout`, `readiness_timeout` |

Delete removes the runtime first and the adapter-owned Session second. Any
partial cleanup failure keeps the Agent record, handle, failure category, and
idempotency tombstone. A retry addresses the same identity. Stop never calls
`delete_session`, so workspace and Session state remain available for restart.

The initial `InMemoryAgentRepository` is explicit application-lifetime state.
It is deterministic and preserves tombstones for the life of one app process,
but does not provide crash recovery or durable Orchestrator metadata. Durable
metadata persistence remains **NOT VERIFIED** and belongs to later work.

## Reproducible validation

```powershell
# Deterministic unit and HTTP contract tests; no Docker or Ollama required.
.\.venv\Scripts\python.exe -m pytest tests/unit/test_agent_lifecycle.py tests/api/test_agent_lifecycle_api.py -q

# Real Docker lifecycle through the same public HTTP API and universal image.
# Ollama is not contacted by this lifecycle-only test.
.\.venv\Scripts\python.exe -m pytest tests/integration/test_agent_lifecycle_api.py -q
```

## TASK-010 conversation coordination

[Non-streaming chat](agent-chat-api.md) adds BUSY. While a turn is active,
start/stop/delete reject with 409. Stop/start preserves committed API messages
and the same native Session. `conversation_recovery_required` is a safe boolean
in Agent responses: an uncertain interaction failure sets it, and start cannot
clear it. Stop/delete remain available for explicit cleanup. Message failures
expose a stable `interaction_code` where known; runtime handles and native
transcript details remain private.
