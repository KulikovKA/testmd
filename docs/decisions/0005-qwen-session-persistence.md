# ADR-0005: Qwen Session persistence ownership

- Status: Accepted
- Date: 2026-09-09
- Decision owners: project maintainers

## Context

Qwen Code `0.23.1` records project-scoped native JSONL transcripts and can
resume a selected native session UUID. TASK-005 proved that the mechanism is
observable, but the 0.6B model did not reliably recall a random token from the
native transcript alone under the tested provider context. The product still
requires one logical Session per Agent, objective multi-turn continuity,
process restart recovery, isolation, diagnosable corrupt state, and explicit
cleanup.

Application code must not understand Qwen transcript paths or schemas. A
public message log may later need semantics that differ from Qwen's internal
context, and TASK-010 owns that API.

## Decision

Use a combined adapter-owned mechanism:

* retain and validate Qwen Code's native project-scoped transcript and opaque
  native session UUID;
* resume that UUID for every later Qwen process;
* store a versioned project-owned JSONL history of successful user/assistant
  turns as authoritative reconstruction context;
* bind both records to a generic `SessionReference` in a versioned manifest
  under a single per-Agent storage directory;
* fail explicitly when required state is missing, corrupt, incompatible, or
  owned by another logical Session; never create a replacement Session during
  `turn`;
* remove all adapter-owned Session artifacts only through explicit
  `delete_session`.

The application port exchanges generic identities and turn values only.
Native UUIDs, paths, transcript validation, Qwen configuration, and process
execution remain inside `QwenSessionAdapter`.

## Consequences

* A new Qwen process and a newly constructed adapter can recover the same
  logical conversation from retained per-Agent storage.
* Qwen's native transcript remains evidence and input to native resume, while
  project history provides an objective, inspectable continuity contract.
* Prompts and responses are stored as Agent data and require the same access
  controls and cleanup as the workspace.
* Model/image/schema compatibility is explicit; changing one does not silently
  reinterpret existing state.
* Full validated history is re-sent to the configured inference provider until
  the explicit PoC history limit is reached. Compression/retention policy is a
  later design concern and cannot silently truncate context.
* The Orchestrator must serialize turns and persist the generic
  `SessionReference`; TASK-009 now coordinates Session create/delete with Agent
  lifecycle; TASK-010 defines process-local public message commits and recovery
  in [ADR-0006](0006-public-chat-commit-and-recovery.md).
* TASK-010 verifies public multi-turn continuity across runtime stop/start;
  durable Orchestrator metadata recovery remains NOT VERIFIED.

## Rejected alternatives

* Native Qwen transcript only: the TASK-005 semantic recall test failed with
  the chosen small model/provider context.
* Project history only with a new native Qwen session per turn: loses native
  Qwen continuity and violates the one-session intent.
* Global in-memory conversation state: cannot survive process restart and
  violates per-Agent ownership.
* Parsing Qwen JSONL in application/domain code: leaks adapter-specific storage
  into the portable boundary.
