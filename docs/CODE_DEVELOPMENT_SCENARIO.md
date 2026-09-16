# Future code-development scenario

The new development Skills describe how an Agent should implement, test, and
review supplied work. They do not grant access to files, a repository, shell,
Git, network, or any mutation. Runtime policy must grant every future action
separately.

```text
Git repository
  -> controlled checkout
  -> Agent /workspace
  -> development Skills
  -> read and analyse
  -> controlled edit
  -> tests/build
  -> git status / git diff
  -> commit
  -> explicit authorised push
```

The repository belongs in `/workspace`, the Agent's isolated writable volume;
the image root filesystem remains read-only. The next implementation stage must
provide narrowly scoped capabilities for controlled checkout, native repository
and file reads, editing, test/build execution, status/diff, committing, and an
explicitly authorised push.

Git credentials must cross a trusted UAR boundary such as `SecretBinding` (or an
equivalent dedicated adapter). They must never appear in a Skill, prompt,
conversation history, workspace repository, logs, or ordinary user text.
Private Git access also needs an explicit allowed network destination. A push is
a separate authorised action, never an effect of selecting a Skill.

The current pinned Agent image has not been changed for Git and its Git client
availability is **NOT VERIFIED**. Verify that prerequisite in the next stage;
do not add Git credentials or arbitrary network access merely to make this
knowledge-only scenario executable.
