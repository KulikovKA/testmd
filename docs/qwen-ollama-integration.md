# Qwen Code / Ollama integration probe

## Scope and component boundary

TASK-005 verifies an executable integration path; it does not implement the
future conversation/session port. Qwen Code is the CLI agent framework running
inside an isolated container. Ollama is an independently operated inference
service that hosts the configured Qwen LLM and is reached over the network. The
Ollama server and model weights are not part of the Qwen Code image.

The probe lives in `qwen_ollama_probe`, outside the domain and application
packages, so no Qwen, OpenAI-compatible, Ollama, or Docker SDK value is added to
the runtime-neutral application contract.

## Versions and authoritative sources

The executable verification on 2026-09-09 used:

* Qwen Code `0.23.1`, official image
  `ghcr.io/qwenlm/qwen-code@sha256:996a12729e25f694254768ac8d3b5f870c54e6ac5825e169299e85a19c78cc10`.
  The package requires Node.js 22 or newer. See the pinned
  [package source](https://github.com/QwenLM/qwen-code/blob/v0.23.1/package.json),
  [model provider configuration](https://github.com/QwenLM/qwen-code/blob/v0.23.1/docs/users/configuration/model-providers.md),
  and [headless mode documentation](https://github.com/QwenLM/qwen-code/blob/v0.23.1/docs/users/features/headless.md).
* Ollama server/client `0.24.0`, running outside the probe container. See the
  official [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
  and [tool calling](https://docs.ollama.com/capabilities/tool-calling)
  documentation.
* Ollama model `qwen3:0.6b`, local list ID `7df6b6e09427`, with a 522 MB
  Q4_K_M weights blob
  `sha256:7f4030143c1c477224c5434f8272c662a8b042079a0a584f0a27a1684fe2e1fa`.
  The official [model page](https://ollama.com/library/qwen3:0.6b) identifies
  this tag as supporting thinking and tools. Qwen's upstream
  [Qwen3 repository](https://github.com/QwenLM/Qwen3) documents thinking mode
  and function calling.

The Qwen Code image digest and package version are immutable inputs. The model
tag is the operator-facing default; record and compare the locally resolved
model ID/blob when exact repeatability is required. Ollama remains a
deployment-owned service and is therefore recorded as a tested version rather
than installed by the Python package.

## Configuration

The safe local example is also present in `.env.example`:

| Environment variable | Safe local default | Purpose |
| --- | --- | --- |
| `QWEN_OLLAMA_BASE_URL` | `http://host.docker.internal:11434/v1` | Explicit OpenAI-compatible endpoint visible from the container |
| `QWEN_OLLAMA_MODEL` | `qwen3:0.6b` | Ollama model tag |
| `QWEN_OLLAMA_API_KEY` | `ollama` | Non-secret placeholder for local Ollama; replace through runtime secret injection when a gateway requires authentication |
| `QWEN_OLLAMA_REQUEST_TIMEOUT_SECONDS` | `300` | Per-provider request timeout |
| `QWEN_OLLAMA_WALL_TIME_SECONDS` | `360` | Hard Qwen Code process budget |
| `QWEN_OLLAMA_MAX_TOKENS` | `384` | Maximum generated tokens per provider response |
| `QWEN_OLLAMA_MAX_SESSION_TURNS` | `6` | Headless session turn budget |
| `QWEN_OLLAMA_MAX_TOOL_CALLS` | `2` | Tool-call budget |

The endpoint must be an explicit HTTP(S) `/v1` URL without user information,
query, or fragment. The model, credential placeholder, numeric limits, and
minimum session budget are validated before Docker is contacted. Credentials
are passed to the container only through environment variables; the probe does
not persist or print their values. Provider configuration selects Qwen Code's
OpenAI provider, disables retries and telemetry, sets temperature zero, and
passes `reasoning_effort=low`. Each prompt explicitly includes `/think`.

## Reproducible execution

Prerequisites are a running Docker engine, an Ollama service listening on the
configured interface, and the configured model:

```powershell
ollama pull qwen3:0.6b
.\.venv\Scripts\python.exe -m qwen_ollama_probe prompt
.\.venv\Scripts\python.exe -m qwen_ollama_probe tool
.\.venv\Scripts\python.exe -m qwen_ollama_probe session
```

`prompt` first calls `GET /v1/models/{model}` from the same official Qwen Code
container, then runs `qwen -p` with `stream-json` output and requires the final
marker `QWEN_OLLAMA_PROBE_OK`. `tool` mounts a temporary synthetic fixture
read-only, allows only `read_file`, explicitly excludes mutating and shell
tools, and confirms that a `read_file` tool-use event occurred before a
successful result event. `--approval-mode yolo` is safe only in this narrowly
bounded probe because the sole allowed capability is read-only and the mounted
workspace is also read-only. It must not be copied to a broader tool set.

Qwen Code `stream-json` produced newline-delimited events. The observed subset
contained a `system` event with `session_id`, nested assistant `tool_use` data,
and a final `result` event with `subtype=success`. The parser rejects missing
JSONL/result events and treats a provider `[API Error: ...]` embedded in an
otherwise successful Qwen result as a failure.

## Verified behavior and limitations

On 2026-09-09 the `prompt` probe passed from the official Qwen Code container:
the in-container preflight returned HTTP 200 and Qwen Code returned exactly
`QWEN_OLLAMA_PROBE_OK`. The controlled tool probe twice emitted only the
allowlisted `read_file` tool name and ended with a successful Qwen result event.

The 0.6B model did not reliably follow the requested final-text format after
the tool result. This is a model-quality limitation, not evidence that broader
tools should be enabled. The probe therefore asserts the protocol facts owned
by TASK-005: the selected harmless tool was invoked, no unselected tool was
observed, and the Qwen process completed successfully.

The `session` mode created a session, recorded its `session_id`, then invoked a
second process with `--resume <session_id>`. Qwen Code reused the same ID, but
`qwen3:0.6b` did not reproduce the random token from the first prompt. This is
negative evidence for TASK-006: native chat recording and resume mechanics are
observable, but persistent conversational correctness is **not verified** and
must not be claimed until TASK-006 supplies an objective passing test and
defines ownership/cleanup of the artifacts.

TASK-006 subsequently satisfied that condition with a combined native Qwen
transcript and project-owned history mechanism. See
[qwen-session.md](qwen-session.md); this TASK-005 observation remains the
evidence for rejecting native-transcript-only persistence with the 0.6B model.

The default Docker bridge resolved `host.docker.internal` and reached the host
Ollama instance. Reaching a deployment-selected Ollama endpoint from the exact
managed `DockerRuntime` network policy is **NOT VERIFIED**: TASK-004 correctly
rejects a non-empty destination allowlist until an enforcement mechanism
exists. TASK-007 owns that container image/network integration and must repeat
the probe from its final runtime context.

`qwen3:4b-thinking` was also downloaded and attempted first. On CPU it reached
the 360-second Qwen Code wall-time budget (exit status 55) before producing a
final event. The 0.6B default was selected to keep the verification bounded.

## Failure categories

The probe emits structured JSON and uses distinct categories:

* `invalid_configuration`: local URL/model/credential/budget validation failed;
* `ollama_connection`: the in-container preflight or Qwen provider could not
  reach a usable endpoint;
* `ollama_authentication`: HTTP/provider evidence reports 401 or 403;
* `model_unavailable`: the model lookup reports 404 or model-not-found text;
* `qwen_process`: Docker/Qwen could not start or exited nonzero, including the
  bounded wall-time exit;
* `qwen_protocol`: JSONL is absent or has no successful final result event;
* `expectation_failed`: transport completed but the scenario-specific marker or
  required tool event was absent.

Unit tests cover category mapping, validation, JSONL extraction, tool-name
extraction, and the Qwen behavior that may wrap a provider error in a successful
result envelope. Production authentication, TLS, proxy, remote routing, and
corporate certificate trust are outside this local probe and remain **NOT
VERIFIED**.
