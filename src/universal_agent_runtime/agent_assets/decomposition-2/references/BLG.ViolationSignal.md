## BLG.ViolationSignal - Violation signal

> **Trigger:** the parent is already decomposed to a sum equal to or greater than its
> estimate (with defect quota), yet the decomposition conditions are not met — the
> agent may still propose a new structure.

```episteme id="BLG.ViolationSignal" context="BacklogManagement"
UseThisWhen:
  starting decomposition of an Epic or Feature that already has earlier-created children
  not when the estimate budget is not exhausted
Result:
  a standalone signal with a reason; no new decomposition proposed
  distinguishes an exhausted budget from semantic insufficiency
Solution:
  ComputeRemainder:
   before proposing decomposition, compute the sum of earlier-created children plus defects by stream quota (BLG.EstimateConservation)
  SignalInsteadOfPropose:
   if sum ≥ parent estimate and decomposition conditions are not met → signal the user and propose no new decomposition
  StandaloneSignal:
   shape the signal as a standalone message with a reason, not an empty or "normal" answer
  DistinguishFromInsufficiency:
   do not conflate with a refusal for lack of semantics (BLG.ReadinessToDecompose): here semantics may be sufficient — the budget is exhausted
  CreateNothing:
   create nothing (BLG.ProposeNotCreate)
Stop:
  no incorrect structure is emitted for an already-reached estimate; the reason is stated
Checks:
  sum of earlier-created children + defects compared with the parent estimate
  on sum ≥ parent and unmet conditions — a signal, not a new decomposition
  signal contains a reason and is distinguished from a data-shortage refusal
  no objects created
Antipatterns:
  proposing decomposition on an exhausted budget → stop and signal
  silent or empty result → a signal with a reason
  conflating with data shortage → distinguish exhausted budget from insufficient semantics
Continues:
  BLG.EstimateConservation, BLG.ProposeNotCreate, BLG.ReadinessToDecompose (distinguish)
Reopen:
  the signal condition or the warning rule changes
```

### BLG.ViolationSignal:End
