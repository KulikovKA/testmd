## BLG.ProposeNotCreate - Propose, do not create

> **Trigger:** the agent has formed a decomposition structure and may either create
> objects immediately or show them as a proposal; the user cannot tell proposed from
> already-created elements.

```episteme id="BLG.ProposeNotCreate" context="BacklogManagement"
UseThisWhen:
  a decomposition structure is ready and the moment of publication is not yet confirmed
  not when the user has already issued the explicit command "apply changes"
Result:
  a proposal with main attributes, marked by absence of id; previously created children shown separately
  supplies BLG.ExplicitCommit with the confirmed structure to publish
Solution:
  ProposeStructure:
   show the decomposition structure with main attributes (name, description, estimate, ...) as a proposal, creating nothing
  ValidateAllRequirements:
   before offering to save (moving to BLG.ExplicitCommit), make sure each element has passed all applicable requirements (BLG.DecompositionValidation, level rules); if any requirement fails, do not offer to save that element
  MarkByIdentity:
   proposed items = "without id"; previously created items = "with id"
  ShowExistingChildren:
   show previously created child elements separately so they are not re-proposed
  DeferCreation:
   create only in the next step (BLG.ExplicitCommit), on the explicit command "apply changes"
Stop:
  the user sees the proposal distinct from the real backlog; nothing has been created
Checks:
  objects not created before the user's explicit command
  proposed items visually distinct from created ones (by id)
  previously created children shown separately
  duplicates of existing elements not proposed
  an element is offered to save only after it has passed all requirements
Antipatterns:
  creating without a command → create only via "apply changes" (BLG.ExplicitCommit)
  merging proposed and created → mark by id
  duplicating existing children → show existing and skip
  offering to save an element before all requirements pass → validate first (BLG.DecompositionValidation); if a requirement fails, do not offer to save
Continues:
  BLG.EstimateConservation; BLG.EditableProposal; BLG.ExplicitCommit to publish
Reopen:
  the publication rule or the proposal composition changes
```

### BLG.ProposeNotCreate:End
