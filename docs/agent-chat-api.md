# Stateful non-streaming Agent chat

TASK-010 adds message submission and bounded history retrieval. TASK-011 subsequently adds [turn-bound SSE](agent-streaming-api.md) using
the same Session, admission and commit logic. Business Tools, Skills and durable
Orchestrator metadata remain outside this API scope.

## HTTP contract

| Operation | Request | Success |
|---|---|---|
| `POST /agents/{agent_id}/messages` | `{"content":"Remember ORBIT_BLUE"}` | `201`, completed user/assistant pair |
| `GET /agents/{agent_id}/messages` | Optional `after` and `limit` query parameters | `200`, committed messages in ascending sequence |

The POST body accepts only `content`: nonblank text without NUL, at most 16,384
characters and within the configured message limit. The Agent must be `READY`.
The body cannot select another Session, workspace, model, or endpoint.

Both endpoints return this envelope (IDs below are illustrative):

```json
{
  "agent_id": "agent-example",
  "messages": [
    {
      "message_id": "opaque-message-id",
      "turn_id": "opaque-turn-id",
      "sequence": 1,
      "role": "user",
      "content": "Remember ORBIT_BLUE",
      "created_at": "2026-09-09T12:00:00Z"
    },
    {
      "message_id": "another-message-id",
      "turn_id": "opaque-turn-id",
      "sequence": 2,
      "role": "assistant",
      "content": "ACK",
      "created_at": "2026-09-09T12:00:01Z"
    }
  ],
  "next_after": null
}
```

Message IDs and turn IDs are opaque UUID hex strings. The shared admission step allocates a public turn ID before execution; a pair shares that
ID; each message has its own ID. `sequence` starts at 1 per Agent and is the
authoritative order. `created_at` is an aware UTC timestamp: turn execution
start for the user message, completion for the assistant. Wall-clock timestamps
are informational and may be affected by clock adjustment. The pair is visible
atomically only after successful completion; pending and failed attempts have
no message IDs or history entries. `next_after` is always null for POST.

GET defaults to `after=0` and the configured maximum page size. `after` is an
exclusive, nonnegative sequence cursor scoped to the requested Agent. `limit`
must be between 1 and the configured page maximum. `next_after` contains the
last returned sequence only if more committed messages exist. Pages may split
pairs. A cursor beyond the end returns an empty page. History is readable in
any existing lifecycle state, including `BUSY`, `STOPPED`, and `FAILED`;
successful deletion makes it unavailable (`404`).

## Concurrency, lifecycle, and cancellation

`AgentChatService` shares the explicit Agent repository with lifecycle use
cases. It reserves `READY -> BUSY` synchronously before awaiting the separate
`AgentInteraction.turn` port. Concurrent turns for that Agent are rejected
with `409 invalid_state`; there is no queue. Start/stop/delete during a turn
are also rejected with `409`. Different Agents have independent state and
may execute concurrently. This is a single-process, single-event-loop PoC
policy, not a distributed lock.

The application owns each accepted turn task until it finishes. If its caller
is cancelled, the turn continues; `BUSY` remains until its outcome is committed.
Application shutdown drains these tasks before closing adapters. Clients must
inspect lifecycle/history after a lost HTTP response. POST has no idempotency
key: blindly repeating a completed submission would create another turn.
TASK-011 adds SSE delivery over the same owned task. No external job/queue
infrastructure is introduced.

## Failures

All errors use the common redacted `{"error": {...}}` envelope. It includes
`code`, fixed `message`, operation (`message` or `history`), Agent identity,
state and retryability when applicable. Interaction errors additionally expose
the stable `interaction_code`, never raw Qwen/Docker output or exceptions.

| Condition | HTTP / code | Lifecycle and history |
|---|---|---|
| Unknown/deleted Agent | `404 not_found` | No changes |
| Invalid JSON, identifier, body or query | `422 request_invalid` / `message_invalid` | No turn; no changes |
| Agent not READY or already BUSY | `409 invalid_state` | No turn; no changes |
| Public history capacity reached | `409 history_limit` | READY; no truncation |
| Adapter context validation limit | `409 history_limit`, `validation_failed` | READY; no committed turn |
| Inference unavailable | `503 inference_unavailable` | READY after native-state rollback |
| Confirmed bounded Qwen timeout | `504 timeout` | READY after native-state rollback |
| Recoverable tool failure | `502 tool_failed` | READY; no committed turn |
| Missing/corrupt/incompatible state, protocol mismatch, uncertain process/commit failure | `502 interaction_failed` | FAILED; recovery required |

Actual business tools are disabled. `tool_failed` only defines portable policy
for a no-commit, recoverable result; rollback of future external tool writes is
**NOT VERIFIED** and is not promised by this task.

An unrecoverable outcome sets `conversation_recovery_required=true` on the
Agent. Stop and delete remain available; stop/start cannot clear that flag or
create a replacement Session. There is no automated repair endpoint. The
operator can retain evidence for diagnosis or explicitly delete the Agent and
create a different identity. No failure automatically destroys the runtime.

## Public history, native state, and recovery

The API log is a bounded tuple of `Message` values in the explicit process-local
Agent record. It survives stop/start within that application lifetime and is
deleted with the Agent. It is distinct from the adapter's committed JSONL
history, manifest, and native Qwen transcript, which preserve model context.
Successful port results must match the same logical Session and the next
expected turn number; mismatches fail closed rather than fabricate messages.

`QwenSessionAdapter` snapshots native state before a turn and records a pending
marker. A known recoverable failure restores the last committed native and
project state before returning. A crash/failed rollback leaves explicit evidence;
reopen/turn refuses a pending marker. Separate filesystem replacements are not
claimed to be a database transaction. Automatic Orchestrator restart recovery,
including restoring API message IDs/timestamps, is **NOT VERIFIED** because its
metadata store remains in memory. See [ADR-0006](decisions/0006-public-chat-commit-and-recovery.md).

The composed `DockerAgentQwenRunner` selects exactly one running, ownership-
labelled container for the Agent. It executes Qwen there, using the same
non-root user and named workspace volume as lifecycle operations. It does not
create a second runtime. The adapter's host native-state copy is the recovery
authority; each turn synchronizes it into `.qwen-home` in that volume, preserving
business workspace files. Downloaded native state is bounded; traversal,
symlinks, hardlinks, devices, and foreign archive roots are rejected. Backend
selection, labels, archive transfers, SDK objects, native UUIDs and process
commands stay inside composition/adapters; the lifecycle port is unchanged.

The standalone TASK-006 runner remains available for its isolated verification
tests. It is not the HTTP application's execution path. Ollama stays external.
The existing bridge profile is connectivity configuration, not destination
egress enforcement. Custom/non-Docker deployment transports are **NOT VERIFIED**.

## Bounds and redaction

| Environment variable | Default | Meaning |
|---|---:|---|
| `UAR_CHAT_MAX_MESSAGE_CHARACTERS` | 16384 | Maximum input text; cannot exceed 16384 |
| `UAR_CHAT_MAX_RESPONSE_CHARACTERS` | 16384 | Maximum accepted assistant response |
| `UAR_CHAT_MAX_HISTORY_MESSAGES` | 100 | Total per-Agent capacity, including both roles; minimum 2 |
| `UAR_CHAT_MAX_HISTORY_PAGE_SIZE` | 50 | Default and maximum retrieval page size |

Limits must be positive integers. Capacity exhaustion rejects a new turn rather
than truncating model context. The adapter separately enforces its configured
65,536-character context and 8 MiB native-transcript limits; neither is bypassed
by increasing HTTP history capacity. Overlarge successful adapter output is an
indeterminate outcome and requires recovery rather than silent truncation.

Configured injected credential values are replaced with `[REDACTED]` in user
content before interaction and in public/project/native message content before
commit. Message reprs omit content. This is exact-value redaction of known
deployment secrets, not a general detector for arbitrary user-supplied secrets.
Conversation text is Agent data, never an instruction to the control plane.

## Verification and acceptance evidence

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_agent_chat.py tests/unit/test_docker_agent_qwen.py tests/unit/test_qwen_session.py tests/api/test_agent_chat_api.py -q
$env:RUN_QWEN_OLLAMA_INTEGRATION='1'
$env:QWEN_OLLAMA_MODEL='qwen3:1.7b'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_agent_chat_api.py -q
```

| TASK-010 criterion | Evidence |
|---|---|
| POST/GET schemas, order, IDs, timestamps, status/errors | OpenAPI and API tests; contract above |
| READY/BUSY/READY through neutral port | Chat unit tests and dependency-direction tests |
| Same Session with earlier context | Live HTTP Docker/Ollama random-codeword recall after stop/start |
| Per-Agent serialization and isolation | Gated deterministic concurrency/cancellation test; separate Agent histories |
| Explicit public/native/recovery relation | Adapter rollback/pending-marker tests; ADR-0006 |
| Recoverable/fatal failure behavior | Parameterized unit/API tests; fatal flag cannot be bypassed by stop/start |
| Requested-Agent history with configured limits | Pagination, validation, capacity, retention, redaction and deletion tests |
| Deterministic tests and real non-streaming path | Unit/API suites and opt-in live integration test |
