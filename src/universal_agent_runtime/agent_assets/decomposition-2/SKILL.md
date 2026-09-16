---
name: backlog-mgmt
description: |
  Управление бэклогом (BLG): декомпозиция «Эпик → Фича → История/Задача»,
  кросстримовой грумминг и массовые изменения атрибутов при участии ИИ-агента.
  Использовать, когда агент декомпозирует, груммит или массово правит элементы
  бэклога и нужно выбрать корректный ход и проверить его выполнение.
---

# LPF: Backlog Management (BLG) — minified

**Depends on:** none (self-sufficient — no skill dependency).
**Bounded context:** backlog management — decomposition, cross-stream grooming, and
bulk attribute change of backlog elements (Epic → Feature → Story/Task) in a
task-management system with an AI agent ("digital assistant").
**Runtime package:** `.agent/skills/decomposition-2/`. This is the complete,
immutable runtime projection of the original `backlog-mgmt` carrier; its bundled
`references/` directory is the only source available during execution.
**Readiness mode:** `source-faithful` — declared **collectively** for the compacted
package (faithful to the source requirements, not `case-validated`); there is no
per-card status.

## When to load which pattern

| Situation | Load |
|---|---|
| An element lacks a semantic part ("for whom"/object/verb), or its acceptance criteria duplicate the name/description | `references/BLG.ReadinessToDecompose.md` |
| The agent creates objects prematurely; proposed and created items are not distinguished | `references/BLG.ProposeNotCreate.md` |
| The sum of child estimates diverges from the parent estimate | `references/BLG.EstimateConservation.md` |
| Child attributes are filled unpredictably; mandatory fields are empty | `references/BLG.AttributeInheritance.md` |
| Changes are published without confirmation; a partial failure stays opaque | `references/BLG.ExplicitCommit.md` |
| A proposal is taken as final; edits drift from the decomposition rules | `references/BLG.EditableProposal.md` |
| Grooming slides between levels or loses the time horizon | `references/BLG.HorizontalGrooming.md` |
| The user does not understand why an element was proposed | `references/BLG.ExplainableRecommendation.md` |
| Actions are proposed without regard to rights; an unreachable action dead-ends | `references/BLG.AccessScopedAction.md` |
| A bulk change is applied at once, without selection, validation, or confirmation | `references/BLG.ControlledBulkChange.md` |
| The selection result overloads the screen; the user's context is not accounted for | `references/BLG.BoundedSelection.md` |
| Conditions are given as plain text; a QL query must be built | `references/BLG.TextToQL.md` |
| The agent's rights diverge from the user's rights | `references/BLG.RoleScopedAuthority.md` |
| Objects created by AI cannot be distinguished from human-created ones | `references/BLG.AIActionTraceability.md` |
| Decomposition may violate business rules; completeness/consistency is checked manually | `references/BLG.DecompositionValidation.md` |
| The estimate budget is exhausted but the decomposition conditions are unmet | `references/BLG.ViolationSignal.md` |
| Work breaks off after an object; starting from an open element needs manual input | `references/BLG.WorkflowContinuation.md` |

## Navigation rule

Start from the working difficulty, not from file order. Load the matching card and
follow its `Continues`/`Reopen` lines when a needed result is missing. A logical
dependency is a result relation, not a prescribed sequence.

## Single surface

`references/` is the canonical surface of this projection; `SKILL.md` is routing-only.
There is no monolith and no reader-facing form. This skill is a compacted runtime
projection whose complete local source is `.agent/skills/decomposition-2/`.

## Consumer-normative surface

The reading agent's obligatory surface is this `SKILL.md` (routing + collective
readiness) plus the `episteme` block of the card it routes to (the method,
`Continues`/`Reopen`). `references/BLG.QLReference.md` is reference-only;
reverse-render is out of scope for a skill consumer.
