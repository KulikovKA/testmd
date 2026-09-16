## BLG.AccessScopedAction - Access-scoped action

> **Trigger:** the agent proposes actions without accounting for access rights, and
> an unreachable action leads the user into a dead end.

```episteme id="BLG.AccessScopedAction" context="BacklogManagement"
UseThisWhen:
  grooming where an action must be chosen for a proposed element
  not when an action is offered without regard to rights
Result:
  a proposed action reachable under the user's current rights
  supplies BLG.ExplainableRecommendation — each action carries a reason
Solution:
  DetermineRights:
   determine the user's rights on the target stream (BLG.RoleScopedAuthority)
  OwnStream:
   own stream → propose "create epic"
  ForeignStream:
   foreign stream or no access → propose "file РДС" (a request to an adjacent stream)
  WithAccess:
   with access, propose "clone epic" and "file linked epic"
  NoDeadEnds:
   never propose an unreachable action; every recommendation carries a reason (BLG.ExplainableRecommendation)
Stop:
  every offered action is reachable; none leads to a denial
Checks:
  action chosen under the user's rights
  no access → "file РДС", not "create"
  with access → "clone" and "linked epic"
  unreachable action not proposed
Antipatterns:
  action without a rights check → rights are the first filter
  "create" in a foreign stream → file РДС
  one option for all cases → distinguish own / foreign / access
Continues:
  BLG.RoleScopedAuthority (rights), BLG.ExplainableRecommendation, BLG.HorizontalGrooming
Reopen:
  the action set or the rights model changes
```

### BLG.AccessScopedAction:End
