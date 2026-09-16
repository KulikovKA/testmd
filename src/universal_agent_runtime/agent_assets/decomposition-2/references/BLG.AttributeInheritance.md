## BLG.AttributeInheritance - Attribute inheritance

> **Trigger:** child elements are filled unpredictably on decomposition; mandatory
> fields remain empty or are filled with anything.

```episteme id="BLG.AttributeInheritance" context="BacklogManagement"
UseThisWhen:
  decomposing Epic → Feature → Story/Task, where each level has its own field dictionary with inheritance rules
  not when a level has no mandatory fields or inheritance rules
Result:
  child attributes filled by parent → child inheritance rules; mandatory fields never left empty
  supplies BLG.ProposeNotCreate / BLG.ExplicitCommit / BLG.ControlledBulkChange with valid attributes
Solution:
  KnowFieldDictionary:
   for each level (Epic/Feature/Story/Task) know the field dictionary and its mandatory fields
  InheritParentToChild:
   fill child attributes by inheritance rules parent → children
  FillMandatory:
   mandatory fields inherited from the parent or set to a default value
  ReportGaps:
   never leave mandatory fields empty; when unable to fill, state what is missing
  ConfigureAttributeSet:
   the attribute set in a proposal is configurable (main by default; extended on request — BLG.EditableProposal)
Stop:
  the created elements pass the task-management system's control and need no rework
Checks:
  inheritance rule applied parent → children
  mandatory fields filled (inheritance or default value)
  empty mandatory fields never allowed without a report
  attribute set configurable, not invented
Antipatterns:
  filling with "anything" → only inheritance or default value
  empty mandatory fields → fill or report the gap
  ignoring field dictionaries → use the level's dictionary
Continues:
  BLG.EstimateConservation (estimates), BLG.EditableProposal, BLG.ExplicitCommit, BLG.ControlledBulkChange
Reopen:
  field dictionaries or inheritance rules change
```

### BLG.AttributeInheritance:End
