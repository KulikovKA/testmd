# ADR-0007: Turn-bound SSE with committed content

- Status: Accepted
- Date: 2026-09-09
- Decision owners: project maintainers

## Context and evidence

TASK-011 requires a verified event protocol while TASK-010 commits successful
message pairs only after native-state validation. A live observation of pinned
Qwen Code 0.23.1 showed early system events, followed later by assistant/result
events. The current adapter buffers execution and returns a committed result;
partial-message mode is a separate upstream option, not enabled in this path.
Exact observed timings and sources are in [agent-streaming-api.md](../agent-streaming-api.md).

## Decision

Use SSE framing over `POST /agents/{agent_id}/messages/stream`, bound to the
single new turn created by that request. Emit started immediately, keep-alive
comments while waiting, and one full committed content event plus completed,
or one terminal error. Clearly label this `committed_response`, not token
streaming. Keep a typed tool-event vocabulary reserved while tools are disabled.

JSON and SSE share the same admission, task ownership and completion logic in
AgentChatService. Allocate the public turn ID at admission; do not expose
native IDs or create another Session. Disconnect detaches delivery but does not
cancel inference or release BUSY. Bound each ASGI send and avoid event queues.

Do not implement replay or subscription-by-ID in this PoC. Reject Last-Event-ID
before admission; clients recover lost-response information from existing
Agent/message APIs. A streaming fetch/HTTP client is required; browser
EventSource automatic reconnection does not fit non-idempotent POST submission.

## Consequences

- Clients receive early acceptance and liveness without mistaking provisional
  Qwen output for a committed reply.
- Existing Session, redaction, concurrency and failure behavior is reused.
- There is no cross-Agent broadcast or subscription routing state to leak.
- No incremental token latency improvement is claimed; HTTP content stays
  buffered until the turn commits. Partial-token transport remains NOT VERIFIED.
- Disconnect can lose terminal delivery; history is the outcome reference.
- No queue, durable event log, new infrastructure, or business Tool behavior
  is introduced. Remote proxy behavior remains NOT VERIFIED.

## Rejected alternatives

- Forwarding raw native JSONL/partial messages: bypasses current commit and
  redaction guarantees and exposes backend-specific or provisional data.
- Splitting a finished response into artificial token chunks: misrepresents
  actual incremental inference delivery.
- Global GET event subscriptions with automatic replay: adds lifecycle and
  ownership state beyond the needs of one non-idempotent turn.
- Polling alone: cannot provide the requested server-to-client event framing.
