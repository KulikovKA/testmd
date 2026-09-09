# ADR-0006: Public chat commit and recovery ownership

- Status: Accepted
- Date: 2026-09-09
- Decision owners: project maintainers

## Context

TASK-010 exposes public messages while ADR-0005 already owns native Qwen
persistence and reconstructed context. Public IDs/timestamps must not be
inferred from Qwen internals. Cancellation or partial commits must not allow
overlapping turns, change the logical Session, or silently hide divergence.

## Decision

Store only successfully completed user/assistant pairs in the process-local
Agent repository. Publish the pair and READY state in one repository save after
validating the port's logical Session and consecutive turn count. Keep API
message identity separate from native Qwen identity and durable context.

Reserve BUSY before awaiting interaction; reject overlapping turns and lifecycle
mutations. The application owns accepted tasks through caller cancellation and
drains them at shutdown. Recoverable interaction failures guarantee no committed
turn and restored native state; uncertainty sets a persistent-in-record recovery
flag that stop/start cannot clear. Never automatically create a replacement
Session. A native pending marker prevents reopening an interrupted write.

Use the existing runtime-neutral interaction port for HTTP turns. Composition
selects the Docker transport that executes inside the lifecycle-owned Agent
container; Docker label resolution and native-state synchronization remain
adapter details. The host native copy is recovery authority, mirrored into the
same Agent workspace volume for Qwen execution.

## Consequences

- API history and native context have explicit separate owners and lifetimes.
- GET during BUSY sees only the previous complete log; failed attempts do not
  invent assistant messages or advance the public sequence.
- Lost HTTP responses require inspecting history before resubmitting; POST is
  not idempotent and no distributed queue is introduced.
- Orchestrator process restart cannot restore public IDs/timestamps with the
  current in-memory repository. This remains NOT VERIFIED; native artifacts
  alone are insufficient to claim full Agent recovery.
- The no-commit guarantee does not imply rollback of future external tool writes.
- Per-Agent native mirrors remain isolated; no additional runtime is created.

## Rejected alternatives

- Reading Qwen JSONL directly in HTTP/application code: leaks native schemas.
- Silently filling a missing API log from a fresh Session: loses identity and
  conceals recovery failures.
- Releasing BUSY when the HTTP request is cancelled: allows a second turn while
  the first adapter thread may still be writing.
- Introducing durable database/queue infrastructure in TASK-010: exceeds scope.
