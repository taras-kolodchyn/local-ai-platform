# Local knowledge, execution, and persistent memory

Status: proposed design for review; implementation has not started.
Evidence reviewed: 2026-09-21.

## Outcome and scope

Extend the existing local stack with a registered LiteLLM vector store,
specialized Hermes execution profiles, reviewed reusable skills, and Mem0 OSS
semantic memory. The user selected the full memory architecture over a
file-only alternative. Supporting both repository work and local documents is
an explicit design assumption, subject to correction during review.

Keep `make up` as the supported entry point. All inference and embeddings use
the existing `local-qwen` and `local-embeddings` aliases. Preserve existing
data, local UI credentials, offline defaults, and loopback port bindings.
Existing technical identifiers and official dependency names are retained
under the user's earlier exception. New workload identities use neutral names.

## Architecture decision

Reuse the current PostgreSQL/pgvector installation and Hermes runtime. Extend
the retrieval service with the HTTP search contract required by the installed
LiteLLM provider. Use Mem0 OSS through the Hermes memory plugin, with a separate
database and restricted database role on the same PostgreSQL instance.

Alternatives considered:

- File-only memory is insufficient for the requested semantic, cross-session
  recall and was explicitly rejected.
- A separate Letta execution stack would duplicate the existing runtime and
  introduce another lifecycle and storage integration. It is not selected.
- The upstream LiteLLM pgvector reference server is useful contract evidence,
  but deploying it would create a second knowledge ingestion path. Reuse the
  current retrieval pipeline instead, with explicit compatibility tests.

Logical flow:

1. Repository and text-document ingestion writes the existing knowledge store.
2. LiteLLM routes vector searches to an authenticated retrieval adapter.
3. A local execution service runs isolated Hermes profiles through LiteLLM.
4. Each profile retrieves knowledge and uses Mem0 for scoped persistent facts.
5. Mem0 obtains inference and embeddings through LiteLLM and persists vectors
   in the separate memory database; it does not use a hosted memory service.

## Knowledge and Vector Store contract

Create a persistent store registry mapping opaque store IDs to exact
repository/branch scopes. Never derive SQL identifiers or filesystem paths from
store IDs. Store scope is mandatory and must not widen when a client supplies
additional filters. Preserve source path, revision, line numbers, and chunk ID
in results so answers can cite evidence.

The adapter must implement listing, retrieval, and search using the actual
installed LiteLLM 1.102.0 provider's request and response schema. Search must
bound query length, result count, response size, and execution time. Unknown
IDs return 404; malformed filters return 400; missing credentials return 401.
Unsupported filter operators fail explicitly rather than being ignored.

Register stores idempotently in LiteLLM and assign access only to approved
profile keys. The adapter credential is backend-only. Validate authorization
through the gateway as well as directly at the adapter. A denied store must
not become accessible by calling the backend with an ordinary client key.

Existing incremental ingestion remains the source of truth. Support local code,
Markdown, and plain-text documents through that pipeline. This design does not
promise PDF/OCR parsing or browser file upload: those require separate parsing
and lifecycle contracts. Do not label unsupported upload operations as working.
Removing a gateway registration does not delete indexed source data.

## Execution profiles and gateway integration

Provide three profiles: `development`, `review`, and `research`. Each has its
own persistent home, history, memory scope, prompt, and scoped gateway key.
Never run two processes against the same profile home at once.

- `development`: repository analysis and proposed changes in a dedicated
  writable scratch directory; the original repository remains read-only.
- `review`: read-only source inspection and verification guidance.
- `research`: document retrieval and answers with source references.

Expose actual execution endpoints through the LiteLLM A2A gateway, rather than
creating registry entries without a running backend. A small local execution
service owns dispatch, timeout, cancellation, and profile locks. It adapts the
pinned Hermes runtime to the gateway's installed protocol contract. Run one
generation workload at a time initially; bound queued requests and return an
explicit busy response when full. This avoids simultaneous heavy inference on
the existing Mac while retaining independent profiles.

Start with synchronous text task execution. Do not advertise streaming,
attachments, background scheduling, or delegation until implemented and tested.
Persist task status and reject or clearly mark interrupted tasks after restart;
never replay potentially effectful work automatically. Skill content must not
imply access to tools the profile lacks.

## Persistent memory

Use the Hermes Mem0 plugin in OSS mode. Configure both language and embedding
clients to use the local gateway aliases, with the actual embedding dimension
(currently 1024) validated against the running endpoint. Pin the Mem0 package
and its tested dependencies during implementation; no floating installation.

Use a dedicated memory database and role. The existing read-only knowledge MCP
role must not acquire access to conversational memory. Separate collections
per profile provide the default isolation boundary. Profile IDs and user scope
are supplied by trusted configuration, never taken from task text.

Retain bounded profile notes for immediate session context; Mem0 owns semantic
facts and retrieval. Session history remains separate from extracted facts.
Shared project knowledge comes from the knowledge store; personal memories are
not automatically shared among profiles. An explicit future sharing operation
can add reviewed facts to shared knowledge.

Support add, search, update, and delete with real persistence. A deletion must
remove the fact from semantic results and any mirrored bounded notes; describe
retained session history separately. Restart tests must prove recall in a new
session, not merely within the current prompt. Explicit add/delete operations
are acknowledged only after successful persistence. If memory fails, ordinary
tasks may continue with a visible degraded-memory status, without claiming a
fact was saved. Disable hosted telemetry and verify destinations during testing.

## Skills and provenance

Select the upstream Superpowers systematic-debugging,
test-driven-development, and verification-before-completion skill workflows.
Pin a reviewed commit and retain the MIT license and provenance. Install into
project-local profile directories, not the user's global configuration.

Review referenced files and scripts as well as SKILL.md. Adapt host-specific
tool references to the actual profile toolset, documenting modifications.
Keep the read-only profiles honest: development workflows can propose tests
and patches but must not imply they modified the source checkout. Preserve
upstream legal attribution; use neutral names for new runtime metadata.
Installation must be repeatable and preserve locally modified skill copies.

## Startup, migration, and recovery

Extend bootstrap and `make up` to provision the memory database, profile homes,
scoped keys, backend credentials, vector registrations, and execution routes
idempotently. Provision dependencies in order and avoid a startup cycle:
PostgreSQL and gateway first, then scoped credentials, retrieval/execution,
then registration and end-to-end readiness checks.

Migrations are additive. Existing knowledge, gateway records, and credential
files must survive repeated startup. Keep secrets in ignored files with mode
0600 or mounted secrets, not command arguments, source-controlled configuration,
or logs. New telemetry records only status, duration, counts, and neutral
workload IDs; no task text or memory values.

Rollback removes the new registrations and stops the new execution service,
then restores prior configuration. It preserves both knowledge and memory
databases for recovery; no automatic volume reset or destructive migration.

## Acceptance criteria

1. Repeated `make up` succeeds without duplicate profiles, stores, or keys.
2. Gateway vector search returns the indexed fixture with correct citations;
   unauthorized keys and cross-scope filters cannot retrieve it.
3. A2A dispatch reaches each real profile and returns a bounded result. A hung
   task times out, releases its lock, and permits the next task to run.
4. Skills are discoverable in the intended profile with pinned provenance.
5. A synthetic fact saved in one session is recalled after restart; another
   profile cannot retrieve it. Updating and deleting the fact are verified.
6. Knowledge indexing does not write to memory collections; memory operations
   do not modify knowledge snapshots or the source checkout.
7. Missing memory/backend services cause explicit failures or documented
   degraded status, never silent success or an indefinite startup wait.
8. Local inference and memory execution require no hosted provider calls.
9. Narrow contract and integration checks pass, followed by `make lint`,
   `make test`, `make smoke-test`, and the extended runtime smoke checks.

## Evidence and limits

Reviewed installed LiteLLM 1.102.0 provider code: pgvector is an HTTP backend
adapter, native memory is key/value storage, and A2A requires execution backends.
Reviewed repository retrieval and Hermes configuration, pinned to
Hermes v2026.9.14. End-to-end compatibility of the proposed additions is not
yet verified; the acceptance checks above are required before claiming success.

Official upstream references:

- [LiteLLM pgvector reference server](https://github.com/BerriAI/litellm-pgvector)
- [Hermes memory provider](https://github.com/NousResearch/hermes-agent/blob/v2026.9.14/plugins/memory/mem0/README.md)
- [Hermes persistent memory](https://github.com/NousResearch/hermes-agent/blob/v2026.9.14/website/docs/user-guide/features/memory.md)
- [Hermes skills](https://github.com/NousResearch/hermes-agent/blob/v2026.9.14/website/docs/user-guide/features/skills.md)
- [Mem0 OSS](https://github.com/mem0ai/mem0)
- [Superpowers](https://github.com/obra/superpowers)
