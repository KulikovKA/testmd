# Skill packaging

Skills are trusted, versioned behavior assets. A selected Skill is delivered to
the owning Agent workspace at `.agent/skills/<skill-id>`. The Qwen adapter adds
a bounded package reference and its effective capabilities to the current Qwen
prompt. The Orchestrator and `AgentRuntime` remain generic: they store and
transport the selected identifiers only.

Each package contains exactly `skill.json` and `SKILL.md` for schema version 1.
The manifest has an identifier, a `major.minor.patch` version, a short summary,
the fixed instruction filename, and a deduplicated list of Tool capability IDs.
The loader rejects unknown fields, unsupported schemas, invalid identifiers or
versions, traversal, links, missing instructions, invalid UTF-8, and oversized
content. Built-in package contents are shipped as application package data.

`task-decomposition@1.0.0` instructs the agent to propose a decomposition,
revise it with new context, and request explicit confirmation before changing
Task data. Cancellation, changed requirements, partial writes, malformed Task
responses, and duplicate risk stop the unsafe action and require a clear next
step. The Skill returns created records after confirmed writes.

A Skill declares the Tool operations it can use, but does not grant them. The
effective set is the ordered intersection of the Agent's selected Tool
capabilities and the package declaration. The restricted Task MCP server from
TASK-012 independently enforces that effective set. A Skill never receives a
Task service URL, credentials, or direct HTTP client.

The package and static fixture tests cover the proposed, revised, rejected,
confirmed, and failure instructions. The opt-in live Qwen/Ollama test selects
this Skill and exercises the restricted MCP path with an explicitly confirmed
proposal. On 2026-09-10 it passed against local Docker, Ollama and
`qwen3:0.6b`. TASK-014 remains the full public-HTTP multi-turn end-to-end proof.
