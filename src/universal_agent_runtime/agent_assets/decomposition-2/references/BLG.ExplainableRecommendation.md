## BLG.ExplainableRecommendation - Explainable recommendation

> **Trigger:** the user does not understand why the agent proposed a given element
> and does not trust the result.

```episteme id="BLG.ExplainableRecommendation" context="BacklogManagement"
UseThisWhen:
  grooming and decomposition where the agent proposes elements
  not when a recommendation is emitted without a reason
Result:
  every proposed element carries its reason (interpreter: the main sign); the full sign list is expandable
  supplies user verification of the grooming/decomposition output
Solution:
  AttachReason:
   for each proposed element state the reason (interpreter): the main sign that drove the proposal
  JustifyPriority:
   for each created child task output a brief justification of the assigned priority
  OfferFullSignList:
   provide an expandable list of the full sign set used in grooming
  ShowReasoning:
   show the agent's reasoning (summary) when forming the answer
  RecommendationNotVerdict:
   a recommendation is a proposal with a reason, not a ready verdict
Stop:
  the user can verify and accept/reject each recommendation consciously
Checks:
  every proposed element has a reason
  main sign shown, full list available
  agent reasoning available to the user
  recommendation not passed off as fact
  assigned priority justified for each created task
Antipatterns:
  list without reasons → a reason per element
  hidden reason → interpreter visible by default
  verdict instead of a proposal → recommendation = proposal with reason
Continues:
  BLG.HorizontalGrooming, BLG.AccessScopedAction; re-entered with BLG.EditableProposal
Reopen:
  the interpreter format or the reasoning requirements change
```

### BLG.ExplainableRecommendation:End
