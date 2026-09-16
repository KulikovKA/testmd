## BLG.HorizontalGrooming - Horizontal grooming

> **Trigger:** grooming slides between levels (epics mixed with features) or loses
> the time horizon (takes everything at once).

```episteme id="BLG.HorizontalGrooming" context="BacklogManagement"
UseThisWhen:
  grooming epics cross-stream (and features by analogy)
  not when mixing levels or leaving the period unbounded
Result:
  a grooming result within one level and a bounded time window, with related elements accounted for
  supplies BLG.ExplainableRecommendation / BLG.AccessScopedAction
Solution:
  StayOneLevel:
   groom only within one level (epic ↔ epic; feature ↔ feature)
  ApplyTimeFilters:
   apply filters: 1 super-sprint back → current → 1 future
  AccountRelations:
   account for related backlog elements: presence of РДС (requests to adjacent streams) and link types
  SelectThenPresent:
   select an epic for grooming via BLG.BoundedSelection; present the result via BLG.ExplainableRecommendation and BLG.AccessScopedAction
Stop:
  the grooming result is comparable and fit for planning
Checks:
  grooming within one level, no epic/feature mixing
  time filter applied (back / current / future)
  related elements (РДС, link types) accounted for
  result bounded and explained (BLG.ExplainableRecommendation)
Antipatterns:
  mixing levels → strictly one level
  ignoring the horizon → time filters
  ignoring links → account for РДС and link types
Continues:
  BLG.BoundedSelection (start), BLG.ExplainableRecommendation, BLG.AccessScopedAction, BLG.RoleScopedAuthority
Reopen:
  grooming rules or the time horizon change
```

### BLG.HorizontalGrooming:End
