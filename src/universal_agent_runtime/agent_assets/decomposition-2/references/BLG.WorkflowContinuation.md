## BLG.WorkflowContinuation - Workflow continuation

> **Trigger:** after decomposition the work stops, or the user opens an element in
> the task system but must manually start the agent and type a request.

```episteme id="BLG.WorkflowContinuation" context="BacklogManagement"
UseThisWhen:
  starting and finishing decomposition scenarios
  not when the user must always type an exact command
Result:
  work continued from the current context; the next undecomposed object proposed, one per request
  supplies BLG.ProposeNotCreate with the next subject
Solution:
  OfferNext:
   on finishing a decomposition, offer to continue with the next epic/feature
  UseElementContext:
   when invoked in the context of an open element, detect its id and proactively offer decomposition without a manual command
  RequireConfirmation:
   ask for explicit user confirmation before starting
  OnePerRequest:
   strictly one object per request/session; do not merge several epics into one output
  AvoidDuplicates:
   account for already created children; do not propose duplicates (BLG.ProposeNotCreate)
Stop:
  the next work step is proposed from context and starts only after confirmation
Checks:
  on finish, the next undecomposed object proposed
  open element recognized and offered as input without manual typing
  start only after explicit confirmation
  strictly one object per request; results not merged
  existing children accounted for, no duplicates
Antipatterns:
  breaking off after one object → propose the next from the list
  requiring manual input with an open element → use context as input
  starting without confirmation → explicit confirmation before start
  merging several epics in one output → one object per request
Continues:
  BLG.ExplicitCommit, BLG.BoundedSelection, BLG.ProposeNotCreate
Reopen:
  the finish scenario or the context definition changes
```

### BLG.WorkflowContinuation:End
