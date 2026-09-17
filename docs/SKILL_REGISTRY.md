# Skill registry and installation

`POST /skills` accepts `multipart/form-data` with one of two source types:

| `source_type` | Fields | Result |
| --- | --- | --- |
| `archive` | `skill_id`: runtime ID; `archive`: one ZIP file | Normalized package installed; HTTP 201 |
| `git` | `skill_id`, `repository_url`, `revision`, `path` | Validated request; HTTP 501 `skill_source_unavailable` in this deployment |

The Git URL must be HTTPS without embedded credentials, query, or fragment.
`revision` is a nonempty bounded ref; `path` is a relative subdirectory without
traversal. No Git transport, network request, or credentials are used by this
deployment. A future `GitSkillSource` can materialize a package into the same
staging area and pass it through the same manifest validation and registry.
Credentials must come from a trusted UAR boundary such as `SecretBinding` and
must never appear in a repository URL, Skill, prompt, conversation, log, or
stored package.

The external ZIP must contain exactly one ordinary Skill folder, for example:

```text
backlog-mgmt.zip
└── arbitrary-original-folder/
    ├── SKILL.md
    └── references/
        └── guide.md
```

`skill_id` in the form is the runtime ID, independent of the ZIP filename,
original folder name, and any text in `SKILL.md`. `references/` is optional.
The archive must not contain `skill.json` or `.uar-source.json` anywhere;
both are reserved UAR metadata. UAR generates the internal `skill.json` with
`version: "1.0.0"`, summary `"Uploaded Skill <skill_id>."`, and empty
`tool_capabilities` and `mutation_tool_capabilities`. Uploaded Skills are
knowledge-only even if their instructions mention tools. `GET /skills` lists
built-in and installed metadata sorted by ID; `GET /skills/{skill_id}` returns
one metadata record. Responses never contain
the registry path or package instructions.

`UAR_SKILL_REGISTRY_ROOT` sets an absolute, non-root deployment directory. The
recommended server value is `/var/lib/universal-agent-runtime/skills`. When
unset, the default is the sibling `skills` directory of the resolved Qwen
session storage root. The service account must be able to create and write the
directory. Layout:

```text
<registry-root>/
└── <skill_id>/
    ├── .uar-source.json
    ├── skill.json
    ├── SKILL.md
    └── references/...
```

The generated manifest and source marker are separate from the external ZIP.
The source marker contains only `"archive"` today. New uploads are staged in
temporary directories under the registry root, validated, then published by a
same-filesystem rename. Existing IDs, including built-ins, return HTTP 409.
There is no update, replace, or delete operation: packages already used by
Agents remain immutable. The registry survives Orchestrator restart, and the
shared catalog reads installed packages on demand, so installation is visible
to `POST /agents` and the Qwen runner without restart.

Archive limits: ZIP bytes at most 8 MiB, total user payload bytes at most 32 MiB,
and at most 256 entries. The entire multipart request is capped at 8 MiB plus
64 KiB for form framing. Extraction rejects absolute or ambiguous paths,
traversal, backslashes, NUL names, duplicate or conflicting paths, links,
special files, multiple package roots, reserved UAR metadata, invalid UTF-8 in
Markdown/JSON, and missing or invalid `SKILL.md`. It does not call `extractall`.
Every component of an **internal ZIP member path** (including the single
top-level folder) is deliberately limited to ASCII
`[A-Za-z0-9][A-Za-z0-9._-]{0,127}`. Spaces and Unicode member names are rejected;
the uploaded ZIP file's own multipart filename is not used as a package path.
This PoC restriction gives UAR one unambiguous, case-insensitive path namespace
without relying on ZIP filename-encoding flags or platform-specific Unicode
normalization. Prepare archive contents with ASCII component names when
uploading a Skill.
Generated internal metadata is excluded from the user payload size and entry
limits on subsequent registry reads. Any failed installation removes staging
and leaves the final Skill directory absent. At runtime the same Skill is
resolved through the package catalog; the intersection of Agent Tool grants
and Skill declarations controls available operations. Uploaded
archive Skills declare no Tools, so they cannot gain one from Agent grants or
their own instruction text.

## One-process demonstration

1. `GET /skills`: verify `my-skill` is absent.
2. `POST /skills` with `source_type=archive`, `skill_id=my-skill`, and the ZIP
   (without `skill.json`): expect HTTP 201.
3. `GET /skills`: verify `my-skill` is present with `source_type=archive`.
4. `POST /agents` with `{"request_id":"my-skill-demo","skills":["my-skill"],"tools":[]}`: expect HTTP 201.
5. `POST /agents/{agent_id}/start`, then send a turn through `/agents/{agent_id}/messages`.
6. Inspect the Agent workspace at `/workspace/.agent/skills/my-skill/` and
   confirm the manifest, instructions, and nested references are present.

Keep one Orchestrator process running across these steps. Its restart is not
required. A server smoke test should repeat the flow with the configured Kata
runtime and corporate Ollama; those integrations are not verified by local
tests on a machine without the corporate environment.
