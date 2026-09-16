## BLG.EditableProposal - Editable proposal before commit

> **Trigger:** a decomposition/grooming proposal is taken as final; edits are made
> without validation and drift from the rules.

```episteme id="BLG.EditableProposal" context="BacklogManagement"
UseThisWhen:
  adjusting a proposed structure before commit (decomposition, grooming, bulk change)
  not after the proposal is committed
Result:
  a proposal as an iterable object, each edit validated by estimate/attribute controls
  supplies BLG.ExplicitCommit with a corrected proposal
Solution:
  TreatAsIterable:
   treat the proposal as an iterable object, not a final
  AcceptTwoEditKinds:
   accept edits of attribute values and of the attribute set ("output attributes X/all/main")
  ValidateEachEdit:
   validate every edit by logical controls per decomposition rules (BLG.EstimateConservation, BLG.AttributeInheritance)
  PreferEditableTable:
   prefer an editable table view with built-in controls
  CommitLater:
   commit the corrected proposal only in BLG.ExplicitCommit
Stop:
  the user has brought the proposal to the needed form with validation at each step
Checks:
  proposal correctable before commit
  both value edits and attribute-set edits supported
  every edit checked by logical controls
  commit only after corrections (BLG.ExplicitCommit)
Antipatterns:
  proposal = final → iterable until commit
  edit without validation → validate each edit
  regenerating instead of editing → edit values/set, do not recreate
Continues:
  BLG.EstimateConservation, BLG.AttributeInheritance (per edit), BLG.DecompositionValidation (checks), BLG.ExplicitCommit (commit)
Reopen:
  the kinds of edit or the representation form change
```

### BLG.EditableProposal:End
