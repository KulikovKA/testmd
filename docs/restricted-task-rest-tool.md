# Restricted Task REST Tool

## Scope

TASK-012 exposes a deployment-configured Task service to Qwen Code through one
local MCP stdio server. The server offers only these operations:

| Tool | Fixed request |
| --- | --- |
| `get_task` | `GET /tasks/{task_id}` |
| `create_task` | `POST /tasks` |
| `create_subtask` | `POST /tasks/{task_id}/subtasks` |
| `update_task` | `PATCH /tasks/{task_id}` |

The tool names, routes, HTTP methods, accepted fields and response schema are
implemented by `task_rest_mcp_server.mjs`. Qwen cannot provide a URL, method,
headers or arbitrary body. The adapter validates IDs, text bounds, enum values,
version and response shape before returning an MCP result.

## Capability selection

Create an Agent with a subset of the exact `tools` capability IDs above. The
lifecycle service transfers that generic capability selection to its owned
runtime. `DockerAgentQwenRunner` reads it from that runtime and gives Qwen only
the matching MCP tool names. A missing Task tool capability produces an empty
MCP discovery result; a Skill and model prompt cannot add a capability.

Qwen runs with `--bare`; therefore the adapter supplies a generated,
adapter-owned `--mcp-config` file explicitly. It contains a single trusted
local `task-rest` stdio server with an `includeTools` allow-list. Native file,
shell, notebook and goal tools remain excluded. `--allowed-tools` contains only
the selected `task-rest__<operation>` names and the turn budget is four calls.

## Deployment configuration

All values are deployment inputs. They are never accepted from a model tool
call.

| Environment variable | Meaning |
| --- | --- |
| `UAR_TASK_API_BASE_URL` | Optional HTTP(S) Task service origin. If absent, no Task MCP server is installed. |
| `UAR_TASK_API_TIMEOUT_SECONDS` | Per-call timeout; defaults to 10 seconds. |
| `UAR_TASK_API_MAX_RESPONSE_BYTES` | Maximum response size; defaults to 65536 and is bounded at 1 MiB. |
| `UAR_TASK_API_TOKEN_SECRET_ID` | Optional runtime secret identifier. It must be paired with `UAR_TASK_API_TOKEN`. |
| `UAR_TASK_API_TOKEN` | Optional credential value, injected only as `UAR_TASK_API_TOKEN`. |

The configured Task origin is added as an explicit runtime network destination
when Docker bridge mode is selected. Docker bridge remains a local integration
mechanism, not destination egress enforcement.

The credential is not written to the Qwen settings, MCP configuration,
workspace asset, session manifest, transcript, public chat response or failure
diagnostic. The adapter redacts the known deployment credential from Qwen
artifacts and public chat values.

## Diagnostics and limits

The MCP server returns a structured, redacted error result with one of
`capability_denied`, `invalid_input`, `not_found`, `timeout`,
`authentication_failed` or `service_failure`. It uses fixed request headers;
`Authorization: Bearer <configured token>` is added only when a token exists.

The service is an adapter boundary. `mock_task_service` remains an independent
test service, and domain/application code does not model Task decomposition or
corporate schemas.

## Validation

`tests/unit/test_task_rest_mcp.py` runs the MCP JSON-RPC protocol inside the
pinned Qwen image and exercises discovery, every allowed operation, denial,
path/method/header injection, oversized responses and service diagnostics.
`tests/integration/test_task_rest_tool.py` is opt-in and verifies that live
Qwen Code discovers and invokes a restricted MCP tool against a real mock Task
service.
