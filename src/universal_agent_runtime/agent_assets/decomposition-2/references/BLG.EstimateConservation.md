## BLG.EstimateConservation - Estimate conservation

> **Trigger:** the sum of child estimates diverges from the parent estimate, the
> parent estimate is distributed arbitrarily, or the parent has no estimate at all.

```episteme id="BLG.EstimateConservation" context="BacklogManagement"
UseThisWhen:
  decomposing an Epic or Feature whose parent and children carry numeric estimates
  not when no estimate invariant is in play
Result:
  the kept invariant "sum of child estimates (+ defect quota) = parent estimate", with a warning on every divergence
  supplies BLG.EditableProposal and BLG.ExplicitCommit with a validated balance
Solution:
  NormalizeUnits:
   the Сфера.Задачи API stores estimates of all types in seconds; convert to days (1 working day = 8 h = 28800 s) and, when not a whole multiple, to hours for display, and back to seconds on write; compare sums in a single unit (seconds)
  RequireParentEstimate:
   check the parent has an estimate; if not, start a dialogue offering to set it and do not decompose before that
  SumChildrenWithQuota:
   sum child estimates in the proposal, increased by defects by stream quota (default 10% unless individual exceptions)
  CompareAndWarn:
   compare with the parent estimate; warn on divergence
  DistributeProportionally:
   distribute the parent estimate proportionally across children
  AllowExistingOverflow:
   previously created children whose sum exceeds the parent estimate — allowed, but with a warning
  Recheck:
   repeat the check after an edit (BLG.EditableProposal) and at commit (BLG.ExplicitCommit)
Stop:
  the parent estimate is set and the balance is verified on every proposal/edit/commit step
Checks:
  units normalized: storage in seconds, display in days/hours, write in seconds
  parent estimate presence checked before decomposition
  sum of children (with defect quota) compared with the parent estimate
  every divergence carries a warning
  distribution is proportional, not arbitrary
  the "existing children > parent" exception stated explicitly
Antipatterns:
  decomposing without a parent estimate → check the field; if empty, request an estimate
  arbitrary distribution → proportional
  silent divergence → warn on any mismatch
  ignoring previously created children
  ignoring the defect quota (default 10%)
  mixing units (API seconds vs UI days) without normalization → normalize: 1 working day = 8 h = 28800 s; storage in seconds, display in days/hours
Continues:
  BLG.DecompositionValidation and BLG.ViolationSignal; re-entered from BLG.EditableProposal / BLG.ExplicitCommit
Reopen:
  the sum rule, the defect quota, or the distribution method changes
```

### BLG.EstimateConservation:End
