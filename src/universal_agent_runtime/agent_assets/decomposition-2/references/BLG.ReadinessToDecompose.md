## BLG.ReadinessToDecompose - Decomposition readiness

> **Trigger:** an Epic or Feature is being prepared for decomposition, but its
> name/description lacks a semantic part ("for whom", object, verb), or its
> acceptance criteria duplicate the name/description.

```episteme id="BLG.ReadinessToDecompose" context="BacklogManagement"
UseThisWhen:
  an Epic (stream owner, КУС) or Feature (team owner, КУК) is about to be decomposed, and its name/description may lack "for whom" / object / verb, or acceptance criteria may duplicate the name/description
  not when all three semantic parts are present and criteria do not duplicate the name/description
Result:
  a sufficient/insufficient verdict on the element, with the identified recipient role shown in the answer
  supplies BLG.ProposeNotCreate with a decomposable element
Solution:
  ExtractRecipientObjectVerb:
   parse from the name/description the three parts: "for whom", object (entity), verb (action)
  CheckAcceptanceCriteria:
   compare acceptance criteria with name/description; flag duplication
  ShowRecipientRole:
   display the identified recipient role explicitly so the user verifies the purpose before the run
  ProceedOrAsk:
   all present → continue to propose the structure (BLG.ProposeNotCreate)
   one part missing → ask exactly one clarifying question on the missing part: role only → "who will be the user?"; verb only → "what exactly should be done?"; both missing → stepwise; criteria = name → "what result should be produced?"
   still insufficient after the dialogue → report it and stop; do not propose decomposition
Stop:
  the element is either confirmed decomposable or honestly reported insufficient, with at most one question asked
Checks:
  "for whom"/object/verb checked before decomposition
  acceptance-criteria duplication with name/description detected
  clarifying questions ≤ 1, targeted at the missing part
  on persisting insufficiency — report, not invention
  identified recipient role explicitly shown
Antipatterns:
  many clarifying questions: ask exactly one on the missing part
  inventing meaning: question or honest stop on insufficiency
  skipping the sufficiency check
  acceptance criteria not compared with name/description
Continues:
  BLG.ProposeNotCreate when the element is decomposable; BLG.ViolationSignal to distinguish semantic insufficiency from an exhausted budget
Reopen:
  the list of checked parts or the one-question limit changes
```

### BLG.ReadinessToDecompose:End
