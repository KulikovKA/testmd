# ADR-0008: Restrict Task service access through an adapter-owned MCP server

## Status

Accepted in TASK-012.

## Context

Qwen Code needs selected Task operations for a configured Agent. Giving the
model an arbitrary HTTP client would let model arguments choose destinations,
methods, headers and payloads. That would violate the narrow Tool boundary and
would couple the core Orchestrator to a particular Task service.

Qwen Code's `--bare` mode does not load MCP entries from `settings.json` unless
they are also supplied through an explicit `--mcp-config` input.

## Decision

The Qwen adapter creates an adapter-owned local MCP stdio server in each Agent
workspace. Its fixed JSON-RPC tool definitions map the four supported tool
names to validated fixed HTTP operations. Endpoint, timeout, response bound and
optional credential come only from deployment configuration. The credential is
injected into the MCP child process environment and never written to generated
configuration or session storage.

Agent `tools` capability IDs remain generic lifecycle metadata. The Docker Qwen
adapter maps only the four known Task IDs to Qwen's exact MCP tool names. A
Skill, Qwen settings or model input cannot elevate this selection. The generated
Qwen command contains one explicit MCP configuration, one MCP server name and
only selected tool names.

## Consequences

The core domain/application layers remain independent of Task schemas and
corporate service assumptions. Adding an operation requires changing the
adapter's fixed schema, validation, documentation and protocol tests. The
local Docker bridge declaration records the configured destination but does not
provide production egress enforcement.
