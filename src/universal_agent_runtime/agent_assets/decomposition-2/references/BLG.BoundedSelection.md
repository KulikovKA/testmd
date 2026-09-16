## BLG.BoundedSelection - Bounded selection

> **Trigger:** the selection result overloads the screen, or the user's context
> (their stream, current/future period) is not taken into account.

```episteme id="BLG.BoundedSelection" context="BacklogManagement"
UseThisWhen:
  the selection step in any scenario (decomposition, grooming, bulk change)
  not when the result set is already bounded and context-aware
Result:
  a bounded, user-relevant set of elements (limit 10 with a warning and pagination)
  supplies BLG.TextToQL output and the entry to grooming/decomposition
Solution:
  FilterByConditions:
   select elements by the user's query conditions
  LimitAndWarn:
   when the result exceeds the limit (10), cap the list and warn; offer pagination / "show more"
  AutoContext:
   auto-substitute context: "my stream" (one space → straight ahead; several → clarify) and "current/future period"
  AllowSkip:
   allow skipping the step when the agent is invoked from a specific element's window
Stop:
  the user finds the needed element in an observable list
Checks:
  result capped at the limit (10) with a warning
  pagination / "show more" available
  user context (stream, period) accounted for
  step skippable when invoked from an element window
Antipatterns:
  overloading the list → limit + warning + pagination
  ignoring context → auto-substitute stream/period
  mandatory selection step → skip when invoked from an element window
Continues:
  BLG.TextToQL (build the query), BLG.HorizontalGrooming, BLG.WorkflowContinuation
Reopen:
  the limit or the auto-context terms change
```

### BLG.BoundedSelection:End
