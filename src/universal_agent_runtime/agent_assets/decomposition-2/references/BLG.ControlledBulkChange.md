## BLG.ControlledBulkChange - Controlled bulk change

> **Trigger:** a bulk attribute change is applied at once, without selection,
> validation, or explicit confirmation.

```episteme id="BLG.ControlledBulkChange" context="BacklogManagement"
UseThisWhen:
  changing attributes (ИФ and others) across a set of elements
  not as a single one-shot application
Result:
  a two-step bulk change (select → explicit apply) with ambiguous values clarified and a rights filter
  supplies BLG.AIActionTraceability after apply
Solution:
  Step1Select:
   step 1 — select elements and state the change ("change ИФ from XXX to ССС for all/selected")
  ClarifyAmbiguity:
   on an invalid/nonexistent dictionary value, clarify ("did you mean ХХХ?")
  PreviewList:
   show the list of elements to change with mandatory fields before apply
  Step2Apply:
   step 2 — apply only on the explicit command (BLG.ExplicitCommit)
  RightsFilter:
   change only elements the user has rights to (BLG.RoleScopedAuthority)
  MarkAI:
   after apply, set "created with AI help" (BLG.AIActionTraceability)
Stop:
  the bulk change is controlled and reversible within the selection step
Checks:
  change goes in two steps: select → explicit apply
  ambiguous dictionary values clarified
  list of changed elements shown before apply
  only elements with rights changed
Antipatterns:
  applying at once → two steps with confirmation
  invalid dictionary value → clarifying question
  changing without rights → rights filter
Continues:
  BLG.ExplicitCommit, BLG.RoleScopedAuthority, BLG.AIActionTraceability, BLG.AttributeInheritance
Reopen:
  the step order or dictionary handling changes
```

### BLG.ControlledBulkChange:End
