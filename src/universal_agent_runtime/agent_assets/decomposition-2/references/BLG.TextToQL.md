## BLG.TextToQL - Text to QL

> **Trigger:** backlog elements must be selected by conditions described in plain
> text (by the user or the agent), without knowing QL syntax.

```episteme id="BLG.TextToQL" context="BacklogManagement"
UseThisWhen:
  the condition is given in natural language in the selection step of any scenario (decomposition, grooming, bulk change)
  not for arbitrary global text→QL outside the fixed scenarios
Result:
  a QL query built only from the supported subset, or a clarifying question when data is missing
  supplies BLG.BoundedSelection with an executable query
Solution:
  ExtractConditions:
   extract from the description the attributes (fields), values, "and"/"or" links, and negations
  MapToSubset:
   map them to the supported QL subset (operators, fields, functions) — exact composition in BLG.QLReference
  BuildWithPrecedence:
   build the expression honoring precedence (and stronger than or) and parentheses
  AskNotGuess:
   on insufficient data (no stream/period/value), ask a clarifying question; never substitute values silently
  RespectBoundary:
   check against the subset boundary; mark anything beyond it as "future work", do not execute silently
  EmitAsTool:
   emit QL as a separate tool/skill (not inline reasoning text) and execute it via the system; pass the result to BLG.BoundedSelection (limit, pagination, auto-context)
  AliasesOptional:
   allow aliases ("my stream", "current СС") as optional future work
Stop:
  the selection is specified in plain text with a correct, subset-bounded query
Checks:
  query built only from the supported subset of operators/fields/functions
  on missing data — a clarifying question, no value substitution
  and/or precedence and parentheses honored
  QL emitted as a separate reusable tool/skill
  anything beyond the subset marked "future work", not executed
  execution result bounded by limit and auto-context (→ BLG.BoundedSelection)
Antipatterns:
  guessing a missing value → clarifying question
  using operators/functions beyond the subset → strict boundary
  a universal arbitrary-text translator → fixed scenarios; the rest is "future work"
  one-off inline generation → a separate reusable tool/skill
Continues:
  BLG.BoundedSelection; BLG.HorizontalGrooming; optional aliases as future work
Reopen:
  the subset or the scenario set expands; the QL manual changes
```

### BLG.TextToQL:End
