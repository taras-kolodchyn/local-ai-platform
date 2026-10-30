# Local knowledge and persistent profiles

Implementation versions: LiteLLM 1.102.0, Hermes v2026.9.14, Mem0 2.1.0,
PostgreSQL 17.11, pgvector 0.8.6. Validation date: 2026-09-21.
End-to-end acceptance is recorded in the [verification report](workspace-verification.md).

## Start and use

`make up` starts the existing stack, indexes its fixture, provisions memory and
execution profiles, registers routes, and runs bounded smoke checks.

Send a task through the gateway without putting credentials in shell arguments:

```sh
printf '%s\n' 'Explain the indexed retry policy and cite its source.' | make workspace PROFILE=research
printf '%s\n' 'Review the indexed service architecture.' | make workspace PROFILE=review
printf '%s\n' 'Propose a regression test for the retry policy.' | make workspace PROFILE=development
```

Profiles see a read-only generated source snapshot. Startup excludes hidden paths,
credential filenames, symbolic links, oversized/binary files, and recognized secret
patterns; update the snapshot by rerunning startup after source changes. Development can prepare patches under
`/opt/data/scratch` inside its private state volume. Review and research have no
local write-capable file tool. All profiles can use their own semantic memory.
The gateway console at http://127.0.0.1:4000 shows registered stores and routes.
Store IDs and execution IDs are in the ignored `.local/workspace-registry.json`.
Run `make up` after indexing a new scope to reconcile store registration and all
profile permissions.

## Knowledge, skills, and memory

Knowledge is the existing incremental source index. Each registered vector store
binds to one repository and branch; search results include source coordinates.
Input support is code, Markdown, and plain text. File upload, PDF/OCR parsing,
streaming tasks, and autonomous background scheduling are not implemented.

Skills are reviewed adaptations of three MIT-licensed Superpowers workflows at
commit `5bf4e78011075bcfc0dc295f0724994cd123ee71`. The local manifest records
hashes, modifications, and profile selection. Rerunning installation preserves
local edits. No global skill installation is changed.

The dashboard Skills catalog is separate from runtime installation. Startup also
registers three pinned upstream source references there; their descriptions identify
which profiles use the reviewed local adaptations. Installing from the catalog
retrieves upstream content and requires network access; local profile execution
continues to use the already-installed adaptations offline. Each execution route
is associated with its existing profile key so the dashboard reports Active.

Mem0 OSS stores semantic facts in `workspace_memory`, using separate PostgreSQL
roles and schemas for development, review, and research. Inference and embeddings
use the existing local aliases. Bounded automatic file-memory mirroring is
turned off to prevent deleted facts from lingering in another injected store;
reviewed profile instructions remain the immediate session guidance. Historical
conversations and Mem0 change history remain in each private state volume.
Deleting a fact removes it from semantic recall, not historical conversations.

The runtime exposes `mem0_add`, `mem0_search`, `mem0_update`, and `mem0_delete`.
Ask a profile to remember, find, correct, or forget a fact; a tool error is not a
successful save. Existing memory dimensions are checked before initialization;
a mismatch requires an explicit migration, never automatic table replacement.

## Limits and access

One generation task runs at a time across profiles. Each backend admits one
active task and at most three waiting tasks. Queue wait is bounded to 10 seconds,
execution to 45 seconds, input to 16000 characters, and output to 24000 characters.
Long work must be broken into smaller requests; timeouts are reported explicitly.
Interrupted tasks are marked failed on restart and are never replayed.

The backend requires both its gateway-only credential and a caller credential.
Backends store only the SHA-256 verifier of the shared console credential; its raw
value stays on the host and is never mounted into a profile.
The supplied CLI sends the caller's scoped key through `X-Workspace-Key` in
addition to ordinary gateway authentication. This compensates for the installed
A2A gateway's unrestricted behavior when grants are absent. Discovery metadata
may be visible to authenticated gateway clients; execution still requires the
backend check. The gateway can translate backend access failures into HTTP 500;
such a response is a rejected invocation, not successful execution.

Profile backends use only the internal data network and each sees only its own
state/configuration. Their HTTP transport rejects non-local destinations.
Secret values and task text are not written to service logs. Session history is
private persistent application data, separate from service logs.

## Checks and recovery

```sh
make lint
make test
make smoke-test
make vector-smoke
make memory-smoke
make workspace-smoke
```

`memory-smoke` uses a unique synthetic fact and verifies fresh-process recall,
cross-profile isolation, update, and deletion. Database privilege verification
runs during memory provisioning; deterministic tests skip a separate optional
external-database fixture when no test DSN is provided.

SQLite history uses native Docker volumes to avoid first-initialization failures
observed on macOS bind mounts. Profile volumes are external to Compose and survive
`down`, including the existing reset target. Do not remove them to fix startup.

To suspend execution without deleting data:

```sh
docker compose --profile workspace stop workspace-development workspace-review workspace-research
```

Rollback restores the previous Compose/startup configuration and unregisters only
the IDs listed in the workspace registry. Preserve PostgreSQL, all profile state
volumes, and ignored credential/configuration files. Rerunning provisioning reuses
valid credentials and existing store/profile identities.

References: [Mem0 provider](https://github.com/NousResearch/hermes-agent/blob/v2026.9.14/plugins/memory/mem0/README.md),
[upstream skills](https://github.com/obra/superpowers/tree/5bf4e78011075bcfc0dc295f0724994cd123ee71),
[vector backend contract](https://github.com/BerriAI/litellm-pgvector).
