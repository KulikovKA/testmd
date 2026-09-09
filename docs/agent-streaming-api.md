# Turn-bound SSE with committed response content

TASK-011 exposes an event stream for one accepted Agent turn. This is **not
token streaming**: the response text is buffered until the existing interaction
adapter validates and commits the turn. Start/keep-alive frames can arrive while
inference is running. No tool execution or new business behavior is added.

## Evidence and protocol selection

Before implementation, the existing pinned Qwen Code `0.23.1` execution was
observed through Docker's streaming stdout transport using the live TASK-010
two-turn HTTP scenario. Only event types and elapsed times were inspected; raw
prompts, output and native identifiers were not retained in the evidence log.

| Turn | system/init | stream_event | assistant events | result/success |
|---|---:|---:|---:|---:|
| First | 2.047 s | 2.109 s | 11.234, 11.297 s | 11.313 s |
| Resumed | 1.984 s | 2.016 s | 14.719, 15.250 s | 15.266 s |

These are observations of one local run, not performance guarantees. The
production `DockerAgentQwenRunner` uses buffered `exec_run`; `QwenSessionAdapter`
returns only after validating native artifacts and committing its history.

The pinned [Qwen headless documentation](https://github.com/QwenLM/qwen-code/blob/v0.23.1/docs/users/features/headless.md)
distinguishes JSONL events from the additional partial-message mode. The latter
is not enabled or claimed by this implementation. Native thinking, partial
assistant output, and tool arguments are not forwarded to HTTP clients.

The client needs early acceptance, a live connection during potentially long
inference, and an unambiguous committed outcome without polling every few
milliseconds. SSE provides a simple UTF-8 event framing format; the
[WHATWG specification](https://html.spec.whatwg.org/multipage/server-sent-events.html)
defines its `event`, `data`, `id` and comment fields. We use this wire format
over POST with a streaming fetch/HTTP client, rather than native browser
`EventSource`, whose URL-based reconnect behavior does not fit a non-idempotent
submission. [ADR-0007](decisions/0007-turn-bound-committed-sse.md) records the choice.

## Request and response

`POST /agents/{agent_id}/messages/stream`

Request body: `{"content":"Remember ORBIT_BLUE"}`. This uses exactly the
same validation, configured text/history limits, Agent eligibility and Session
as `POST /agents/{agent_id}/messages`. The existing JSON route is unchanged.

Validation and BUSY reservation happen before response headers. Rejected
requests retain the usual JSON error envelope and `404`, `409` or `422` status.
An accepted stream has HTTP `200`, `Content-Type: text/event-stream`,
`Cache-Control: no-store`, `X-Accel-Buffering: no`, and `X-Turn-ID` containing
the newly allocated public turn ID. An intermediary may ignore these headers;
remote proxy buffering remains **NOT VERIFIED**.

Example frame (the public IDs here are illustrative):

```text
id: turn-example:1
event: started
data: {"schema_version":1,"agent_id":"agent-example","turn_id":"turn-example","sequence":1,"data":{"type":"started","content_mode":"committed_response"}}

```

All data events use a versioned `StreamEvent` envelope. The `data` property is
a discriminated union selected by `data.type`, which also matches the SSE
`event` field. `sequence` is consecutive within this stream, not the public
history sequence; `id` is `<turn_id>:<sequence>`. Native Qwen UUIDs never appear.
The public turn ID is allocated by the shared chat service and is also stored
on both committed messages.

| Type | Data fields beyond `type` | Semantics |
|---|---|---|
| `started` | `content_mode: "committed_response"` | First event, sequence 1; turn accepted |
| `content` | `content`, `message_id` | Entire committed assistant response, sequence 2 |
| `completed` | `message_ids: [user_id, assistant_id]` | Success terminal, sequence 3; history already committed |
| `error` | `code`, optional `state`, `retryable`, optional `interaction_code` | Failure terminal, sequence 2; no content or success terminal |
| `tool` | `tool_name`, `phase: started/completed/failed` | Reserved typed vocabulary; never emitted while tools are disabled |

Success is `started -> content -> completed -> EOF`; a turn failure is
`started -> error -> EOF`. There is exactly one terminal event on a fully
delivered stream. No events follow it. JSON encoding escapes model text,
including newlines, so it cannot inject SSE control fields. Content is the
same redacted, size-checked value exposed in message history. Error events
carry only stable application codes, never exception strings or native output.

OpenAPI describes the wire response as `text/event-stream` and includes the
standalone JSON schema for one data envelope under the operation's
`x-event-schema` extension. Extract that extension as a complete JSON schema
when validating event payloads; its `$defs` references are local to that schema.

## Ownership, cancellation, and reconnection

There is no global event bus, Agent-wide subscription, or attach-by-turn-ID
endpoint. A response is bound to the newly accepted turn of its own request.
Caller-provided `turn_id` or `session_id` fields are rejected; no request can
attach to another Agent's running turn. A GET events route is intentionally
absent. Existing local API access assumptions still apply: this task does not
add authentication or tenant authorization.

The shared `AgentChatService.begin` reserves BUSY and starts one owned task.
Both JSON and SSE use it; they cannot race into a second Session turn. Lifecycle
start/stop/delete and another message for the same BUSY Agent receive `409`.
Other Agents remain independent. Success/failure and native-state rollback use
the unchanged TASK-010 policy.

Disconnect, response cancellation or slow-consumer timeout stops event
delivery, **not** the accepted turn. Its application-owned task retains BUSY
until completion; shutdown drains it before closing adapters. No terminal
delivery is guaranteed after a broken connection. An error after HTTP headers
uses an `error` event, not a new HTTP status.

PoC reconnection policy: **no replay or automatic resubmission**. Any
`Last-Event-ID` header is rejected with `409 replay_not_supported` before
starting a new turn. After a lost connection, inspect the Agent and its message
history; use the public turn ID when headers were received. A repeated POST
without that header is a new submission, not reconnection. Process restart
recovery and durable event replay are **NOT VERIFIED** and not implemented.

## Bounded delivery

| Configuration | Default | Meaning |
|---|---:|---|
| `UAR_STREAM_HEARTBEAT_SECONDS` | 15 | Interval while waiting for the turn result |
| `UAR_STREAM_SEND_TIMEOUT_SECONDS` | 10 | Maximum wait for each ASGI network send |

Both must be finite positive numbers. Heartbeats are `: keep-alive` comments
with no event ID or sequence. They are generated on demand and never queued.
There is no producer/consumer event queue, replay cache or cumulative heartbeat
buffer. A slow transport retains at most the bounded turn result and one
serialized frame. Response content obeys `UAR_CHAT_MAX_RESPONSE_CHARACTERS`;
serialization adds bounded JSON escaping/envelope overhead. HTTP/network
buffers outside the application are not claimed to be under this limit.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests/api/test_agent_streaming.py tests/unit/test_agent_chat.py tests/api/test_agent_chat_api.py -q
$env:RUN_QWEN_OLLAMA_INTEGRATION='1'
$env:QWEN_OLLAMA_MODEL='qwen3:1.7b'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_agent_streaming.py -q
```

The live test uses an ephemeral loopback TCP port with Uvicorn and a streaming
HTTPX client. On receipt of `started`, a second HTTP request proves the Agent
is still BUSY and history is empty. It observes keep-alive frames, then the
single committed content frame, verifies history IDs, and submits a JSON turn
that recalls a random codeword from the SSE turn. This proves early lifecycle
delivery and shared context; it does not claim incremental token delivery.

Deterministic tests cover order and terminal events, error mapping, redaction,
SSE frame injection, cross-turn attachment rejection, disabled replay,
heartbeat, JSON/SSE overlap, ASGI disconnect, network failure, slow consumer,
configuration limits, and continued completion after disconnect.

## TASK-011 acceptance mapping

| Criterion | Evidence |
|---|---|
| Inspect current Qwen behavior before protocol selection | Type-only live observation above, pinned upstream documentation, ADR-0007 |
| Typed turn-bound event envelope | StreamEvent discriminated schema and OpenAPI/API tests; tool type explicitly reserved |
| Order, terminal, buffer, disconnect, heartbeat and reconnect policy | Contract above plus deterministic event/ASGI/network tests |
| Reuse Session and lifecycle without duplicate business logic | Shared begin/completion; JSON/SSE overlap tests; live SSE-to-JSON recall |
| Agent isolation, redaction and size limits | No attach/broadcast endpoint; foreign-ID rejection; redaction/frame-injection tests; shared bounded chat result |
| Deterministic complete/error/disconnect/slow-consumer tests | tests/api/test_agent_streaming.py |
| Real delivery and honest buffering claims | TCP/Uvicorn/Docker/Ollama test; started observed while BUSY; one committed content event; no token claim |
| Existing JSON API remains functional | Full regression suite and live JSON follow-up after SSE |
