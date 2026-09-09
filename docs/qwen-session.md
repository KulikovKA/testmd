# Persistent Qwen Session adapter

## Scope and boundary

TASK-006 implements the runtime-neutral `AgentInteraction` port and the
Qwen-specific `QwenSessionAdapter`. It does not add HTTP endpoints, Agent
lifecycle orchestration, a universal agent image, streaming, Skills, or
business Tools.

The application boundary exposes only `AgentId`, `SessionId`,
`SessionReference`, turn values, observations, and stable
`InteractionFailure` categories. Native Qwen session UUIDs, JSONL transcript
paths, Qwen settings, Docker containers, and SDK failures stay inside the
adapter.

## Chosen persistence mechanism

The adapter uses the combination recorded in
[ADR-0005](decisions/0005-qwen-session-persistence.md):

1. Qwen Code chat recording remains enabled. The first turn uses
   `--session-id <native UUID>` and later turns use `--resume <native UUID>`.
2. A project-owned manifest binds one logical `SessionReference` to that
   opaque native UUID, the pinned Qwen Code version/image, model, turn count,
   and a relative transcript location.
3. A project-owned append-shaped JSONL history records successful user and
   assistant turns. The complete validated history is supplied to every turn
   as authoritative context. This makes remembered facts objective even when
   a small model or provider context limit does not reliably reconstruct them
   from native Qwen history alone.

The native transcript is still mandatory after the first successful turn.
The adapter will not silently fall back to a fresh Qwen session when it is
missing or invalid.

## Storage layout and ownership

For an explicitly configured `storage_root`, each validated Agent identity
owns exactly one directory:

```text
<storage_root>/
`-- <agent_id>/
    |-- session-state.json
    |-- history.jsonl
    |-- workspace/
    `-- qwen-home/
        |-- settings.json
        `-- projects/<qwen-project>/chats/<native-session-id>.jsonl
```

`session-state.json` is adapter metadata schema version 1. It contains the
logical Agent/Session identities, Qwen compatibility fields, opaque native
UUID, completed-turn count, and a transcript path relative to `qwen-home`.
It contains neither the Ollama endpoint nor credentials.

`history.jsonl` schema version 1 contains only committed successful turns, in
strict consecutive order. Files are replaced atomically. Prompts and model
responses are persisted in full, so callers must treat the storage root as
Agent data and must never put credentials into conversation text.

The Qwen credential is injected into each ephemeral process through
environment variables and is never written to the manifest, history, settings,
or logs by this adapter. `settings.json` contains the configured endpoint,
model, provider limits, and credential environment-variable name, but not its
value.

## Turn and recovery behavior

`create_session` creates a new empty Session or validates and reopens the
identical existing Session without changing it. A different Session identity
for an occupied Agent directory is a conflict.

For each turn the adapter:

1. validates the manifest, logical ownership, history, compatibility fields,
   transcript path, transcript size, and every non-empty JSONL record;
2. reconstructs bounded authoritative conversation context from
   `history.jsonl`;
3. starts the pinned Qwen Code `0.23.1` image with a short explicit system
   prompt, `/think`, no available tool budget, and either `--session-id` or
   `--resume`;
4. requires a successful `stream-json` result with the expected native
   session UUID and non-empty response;
5. validates/discovers the native transcript, then atomically commits the
   updated project history and manifest.

Every Qwen turn already runs in a new container process. Reconstructing a new
`QwenSessionAdapter` with the same storage root, model, and image therefore
models both process and adapter restart. The live test reopens the adapter
between turns and verifies an unpredictable codeword from the first turn.

A future Orchestrator restart is expected to recover its generic
`SessionReference` and configured workspace identity from its own metadata,
construct this adapter against the same retained storage root, and call
`create_session` to validate/reopen it. Orchestrator metadata persistence and
wiring are not implemented in TASK-006.

Turns for one Session must be serialized by the Orchestrator. Cross-request
queueing/rejection belongs to TASK-010; this adapter does not introduce a
process-global mutable Session registry.

## Isolation and cleanup

Validated identifiers are used only as direct children of the configured
non-root storage directory. A container receives exactly two mounts from one
Agent directory: that Agent's `qwen-home` and `workspace`. It receives no
other Agent directory and no Docker socket. Conversation tools are excluded
and the tool-call budget is zero.

Unit tests prove that two Agents receive different native UUIDs, Qwen homes,
and workspaces; an Agent cannot address another logical Session through the
port. `delete_session` validates ownership, recursively removes that exact
Agent directory, verifies absence, and is idempotent after successful removal.
Connecting this cleanup to Agent delete, and proving stop/start retention
through `DockerRuntime`, remain deferred to TASK-009 and TASK-014.

## Stable failures

`InteractionFailure` exposes only operation, generic Agent/Session identity,
stable code, and retryability:

* `not_found`: required manifest, history, or native transcript is absent;
* `conflict`: an Agent directory already belongs to another Session;
* `corrupt_state`: JSON, history sequence, or transcript records are invalid;
* `incompatible_state`: schema, pinned Qwen image/version, model, size, or
  configured history compatibility check fails;
* `inference_unavailable`: provider connection/authentication/model evidence;
* `timeout`: Qwen Code exits on its bounded wall-time;
* `protocol_failure`: Qwen JSONL has no valid successful result, returns a
  different native session UUID, or creates no unique transcript;
* `operation_failed`: Docker/process/filesystem operation failed;
* `cleanup_failed`: the owned directory could not be removed.

Raw Docker exceptions, Qwen output, endpoints, filesystem paths, and
credentials do not cross the application port.

## Verification

The deterministic suite uses an injected Qwen runner and covers multi-turn
continuity, adapter reopening, native-ID reuse, isolation, cleanup, validation,
missing/corrupt/incompatible artifacts, bounded history, and error translation.

The opt-in live test uses the existing local Docker/Ollama environment:

```powershell
$env:RUN_QWEN_OLLAMA_INTEGRATION="1"
$env:QWEN_OLLAMA_MODEL="qwen3:1.7b"
.\.venv\Scripts\python.exe -m pytest tests/integration/test_qwen_session.py -q
```

On 2026-09-09 it passed with the pinned Qwen Code image, Ollama `0.24.0`, and
the downloaded thinking-capable `qwen3:1.7b` (1.4 GB, local list ID
`8f68893c685c`, weights blob
`sha256:3d0b790534fe4b79525fc3692950408dca41171676ed7e21db57af5c65ef6ab6`):
turn one stored a random codeword,
the first adapter and Qwen process were closed, and a newly constructed adapter
resumed the same native Session and returned the exact codeword on turn two.

The exact managed `DockerRuntime` stop/start lifecycle and future universal
agent image are **NOT VERIFIED** here because they belong to TASK-007,
TASK-009, and TASK-014.
