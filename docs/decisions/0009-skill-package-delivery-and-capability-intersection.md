# ADR 0009: Deliver versioned Skills as trusted packages with capability intersection

## Status

Accepted.

## Context

The first Agent behavior needs to be selected per Agent without placing
task-decomposition branching in the Orchestrator or runtime. Its instructions
must not become an authorization mechanism for Task mutations.

## Decision

Built-in Skills are strict `skill.json` plus `SKILL.md` packages with semantic
versions. The Qwen adapter resolves selected package IDs, copies each package
to the owning runtime workspace, and adds a bounded package reference plus
effective capabilities to the Qwen prompt. It calculates effective Tool
capabilities by intersecting the Agent's Tool selection with the package
declaration.

The Task MCP adapter remains the enforcement point for actual Task operations.
The application stores package IDs in the Agent configuration and passes them
through an opaque runtime environment value; it contains no behavior-specific
branching.

## Consequences

Package data is immutable application content and each Agent receives a private
runtime copy. Malformed or unavailable selected packages fail safely before a
Qwen invocation. Adding a new Skill requires a package and catalog entry, not
a change to lifecycle logic. TASK-014 will provide the live end-to-end evidence
for model compliance with the instructions.
