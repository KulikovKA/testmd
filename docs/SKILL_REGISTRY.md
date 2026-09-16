# Skill registry and installation

`POST /skills` accepts `multipart/form-data` with one of two source types:

| `source_type` | Fields | Result |
| --- | --- | --- |
| `archive` | `archive`: one ZIP file | Validated package installed; HTTP 201 |
| `git` | `repository_url`, `revision`, `path` | Validated request; HTTP 501 `skill_source_unavailable` in this deployment |

The Git URL must be HTTPS without embedded credentials, query, or fragment.
`revision` is a nonempty bounded ref; `path` is a relative subdirectory without
traversal. No Git transport, network request, or credentials are used by this
deployment. A future `GitSkillSource` can materialize a package into the same
staging area and pass it through the same manifest validation and registry.
Credentials must come from a trusted UAR boundary such as `SecretBinding` and
must never appear in a repository URL, Skill, prompt, conversation, log, or
stored package.

The ZIP must contain exactly one UAR package, for example:

```text
my-skill.zip
└── my-skill/
    ├── skill.json
    ├── SKILL.md
    └── references/
        └── guide.md
```

The package directory name must equal the manifest `id`. The strict existing
`skill.json` schema supplies `id`, `version`, `summary`, `tool_capabilities`,
and `mutation_tool_capabilities`. Third party formats without that manifest
are rejected. `GET /skills` lists built-in and installed metadata sorted by ID;
`GET /skills/{skill_id}` returns one metadata record. Responses never contain
the registry path or package instructions.

`UAR_SKILL_REGISTRY_ROOT` sets an absolute, non-root deployment directory. The
recommended server value is `/var/lib/universal-agent-runtime/skills`. When
unset, the default is the sibling `skills` directory of the resolved Qwen
session storage root. The service account must be able to create and write the
directory. Layout:

```text
<registry-root>/
└── my-skill/
    ├── .uar-source.json
    ├── skill.json
    ├── SKILL.md
    └── references/...
```

The source marker contains only `"archive"` today. New uploads are staged in
temporary directories under the registry root, validated, then published by a
same-filesystem rename. Existing IDs, including built-ins, return HTTP 409.
There is no update, replace, or delete operation: packages already used by
Agents remain immutable. The registry survives Orchestrator restart, and the
shared catalog reads installed packages on demand, so installation is visible
to `POST /agents` and the Qwen runner without restart.

Archive limits: ZIP bytes at most 8 MiB, total extracted bytes at most 32 MiB,
and at most 256 entries. The entire multipart request is capped at 8 MiB plus
64 KiB for form framing. Extraction rejects absolute or ambiguous paths,
traversal, backslashes, NUL names, duplicate or conflicting paths, links,
special files, multiple package roots, invalid UTF-8 in Markdown/JSON, and
invalid UAR manifests. It does not call `extractall`. Any failed installation
removes staging and leaves the final Skill directory absent. At runtime the
same Skill is resolved through the package catalog; the intersection of Agent
Tool grants and Skill declarations controls available operations. Installing a
Skill cannot grant a Tool by itself.

## One-process demonstration

1. `GET /skills`: verify `my-skill` is absent.
2. `POST /skills` with `source_type=archive` and the ZIP: expect HTTP 201.
3. `GET /skills`: verify `my-skill` is present with `source_type=archive`.
4. `POST /agents` with `{"request_id":"my-skill-demo","skills":["my-skill"],"tools":[]}`: expect HTTP 201.
5. `POST /agents/{agent_id}/start`, then send a turn through `/agents/{agent_id}/messages`.
6. Inspect the Agent workspace at `/workspace/.agent/skills/my-skill/` and
   confirm the manifest, instructions, and nested references are present.

Keep one Orchestrator process running across these steps. Its restart is not
required. A server smoke test should repeat the flow with the configured Kata
runtime and corporate Ollama; those integrations are not verified by local
tests on a machine without the corporate environment.
