## BLG.AIActionTraceability - AI action traceability

> **Trigger:** from history or audit, objects created by AI cannot be distinguished
> from those created by humans.

```episteme id="BLG.AIActionTraceability" context="BacklogManagement"
UseThisWhen:
  any scenario where the agent creates or changes objects
  not when AI-created objects need no distinction
Result:
  each agent-created object marked "created with AI help", visible in history and audit
  supplies audit and rollback distinguishability
Solution:
  MarkAICreated:
   for each object created via the agent (ЦП), set "created with AI help"
  ReflectInHistory:
   reflect the mark in the object's history and audit
  MarkAfterSuccess:
   set the mark after successful creation (BLG.ExplicitCommit) or bulk change (BLG.ControlledBulkChange)
  DoNotMarkHuman:
   do not mark objects created by a human
Stop:
  the origin of each backlog change is distinguishable; review and rollback are possible
Checks:
  every agent-created object marked
  mark visible in history and audit
  mark set after successful creation
  human-created objects not marked
Antipatterns:
  no mark → mandatory for AI-created objects
  marking before creation → mark after success
  marking human objects → only AI-created
Continues:
  BLG.ExplicitCommit, BLG.ControlledBulkChange (marking follows them)
Reopen:
  the distinguishing mark changes
```

### BLG.AIActionTraceability:End
