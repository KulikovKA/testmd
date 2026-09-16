## BLG.ExplicitCommit - Explicit commit

> **Trigger:** changes are ready to publish but are published without explicit
> confirmation, or a partial creation failure stays opaque.

```episteme id="BLG.ExplicitCommit" context="BacklogManagement"
UseThisWhen:
  the final step of decomposition, grooming, or bulk change
  not before the user issues the explicit command "apply changes"
Result:
  a full result report: previously created (with id), created now (with id), not created (name + system error text), and estimate-sum warnings
  supplies BLG.AIActionTraceability after a successful commit
Solution:
  AwaitExplicitCommand:
   do not publish before the explicit command "apply changes"
  ReportThreeGroups:
   show three groups: previously created (with id), created now (with id), not created (name + system error text)
  ShowEstimateSum:
   show the sum of created child estimates; warn on mismatch (BLG.EstimateConservation)
  RelayErrors:
   the task-management system checks creation correctness; the agent relays a clear error text
  MarkAI:
   after commit, set "created with AI help" (BLG.AIActionTraceability)
Stop:
  the user controls the moment of publication and sees the complete result, including failures
Checks:
  publication only on an explicit command
  created and not-created shown separately
  not-created carry the system error text
  estimate-sum mismatch carries a warning
Antipatterns:
  publishing without a command → only via "apply changes"
  hiding a partial failure → full created/not-created list
  vague error → clear system error text
Continues:
  BLG.AIActionTraceability; preceded by BLG.ProposeNotCreate / BLG.EditableProposal / BLG.ControlledBulkChange
Reopen:
  the report format or the publication rule changes
```

### BLG.ExplicitCommit:End
