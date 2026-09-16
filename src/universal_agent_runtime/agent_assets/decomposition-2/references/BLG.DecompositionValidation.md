## BLG.DecompositionValidation - Decomposition validation

> **Trigger:** a proposed or edited decomposition may violate level business rules
> (inheritance, ИФ/teams/ИС, dates and super-sprints, estimate sum, structure), and the
> check is done manually.

```episteme id="BLG.DecompositionValidation" context="BacklogManagement"
UseThisWhen:
  decomposing Epic → Feature and Feature → Story/Task, before the result enters the backlog
  not when the level rule set is unavailable
Result:
  a Pass/Fail per rule over the full level rule set, violations highlighted, nothing created
  supplies BLG.ProposeNotCreate / BLG.ExplicitCommit with a validated structure
Solution:
  LoadRuleSet:
   keep a configurable business-rule set per transition (Epic→Feature, Feature→Story/Task), loaded from the knowledge sphere, not hardcoded
  ValidateInheritance:
   all parent values reflected in children, no extras
  ValidateTeamsIS:
   completeness of ИФ, teams and ИС, with no extras; the "ИС — team" pair matches the Сфера.Команды reference
  ValidateDates:
   a feature's super-sprint equals the epic's or is one less, not closed; dates within parent and super-sprint/sprint bounds
  ValidateEstimateSum:
   sum of children plus defects by stream quota (default 10% unless individual exceptions) equals the parent estimate
  ValidateStructure:
   epic — 1–2 super-sprints; feature — 1 super-sprint; story/task — 1 sprint; child count not fixed; decomposition frequency; an epic is decomposed only down to the `feature` level (no `story`/`task` elements created when decomposing an epic)
  AccountClosedChildren:
   account for closed children with a resolution other than "Готово" when estimating the remainder
  EmitPassFail:
   output Pass/Fail per rule; highlight violations for the user
  DeterministicCriticalRules:
   check critical business rules that the user cannot override deterministically, regardless of agent behavior (in the agent harness)
  CreateNothing:
   create nothing on validation (BLG.ProposeNotCreate); commit separately (BLG.ExplicitCommit)
Stop:
  the full level rule set has been applied in one pass; violations are highlighted before commit
Checks:
  full level rule set applied in one pass
  inheritance, ИФ/team/ИС completeness, and ИС↔team match checked
  dates and super-sprint/sprint bounds checked
  estimate sum with defect quota compared with the parent estimate
  structural level limits checked; an epic decomposed only down to the `feature` level
  result is Pass/Fail per rule; violations highlighted
  critical rules checked deterministically
  validation creates no objects
Antipatterns:
  eyeballing instead of the rule set → apply the full level set
  hardcoded rules in the agent logic → configurable set from the knowledge sphere
  silent violation → Pass/Fail with highlighting
  validation that writes → validate without creating objects
  skipping critical rules at the agent's discretion → deterministic check
  decomposing an epic past the `feature` level (straight to `story`/`task`) → an epic decomposes to features only; stories/tasks come from decomposing a feature
Continues:
  BLG.ProposeNotCreate, BLG.EditableProposal, BLG.AttributeInheritance, BLG.EstimateConservation, BLG.ExplicitCommit
Reopen:
  the per-transition rule set or the critical-rule list changes; the rule delivery mechanism changes
```

### BLG.DecompositionValidation:End
