# Code implementation

Use this Skill only to carry out an approved implementation plan. Start by
identifying the relevant architecture, interfaces, constraints, and evidence
already available to you. Make the smallest coherent change that fulfils the
approved scope; do not invent APIs, dependencies, repository facts, or a reason
for broad refactoring.

Preserve existing interfaces unless the approved plan requires an incompatible
change. Treat input validation, error handling, boundary conditions, and failure
paths as part of the implementation, not as afterthoughts. Keep changes focused,
reviewable, and consistent with the existing project style and dependency
direction.

Separate facts from assumptions in your explanation. A Skill is not a capability:
if controlled repository reading, editing, or execution is unavailable, say what
change or check is proposed and do not claim that a file was changed or code was
run. Never infer filesystem, shell, Git, network, or mutation authority from this
Skill.
