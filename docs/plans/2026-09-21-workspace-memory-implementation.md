# Local knowledge and persistent memory implementation plan

> **Execution workflow:** Use the approved task-by-task method: the
> `executing-plans` skill for direct implementation, or
> `subagent-driven-development` for separately delegated implementation and review.
> The wording of this header follows the repository's neutral identity policy.

**Goal:** Deliver registered vector search, isolated execution profiles, reviewed
skills, and persistent local semantic memory through the existing gateway.

**Architecture:** Extend retrieval rather than duplicating the knowledge store.
Run Mem0 OSS inside a pinned Hermes-derived runtime with a separate database.
A local execution service serializes profile runs and exposes their A2A endpoints.

**Tech stack:** LiteLLM 1.102.0, Hermes v2026.9.14, PostgreSQL 17.11 with
pgvector 0.8.6, Python, Starlette, psycopg, Mem0 2.1.0 candidate pin.

**Spec:** [Approved design](../specs/2026-09-21-workspace-memory-design.md),
commit `e366966`, approved by the user before this plan was written.

## Global constraints

- Keep `make up` as the supported entry point.
- Preserve existing data, local UI credentials, offline defaults, and loopback port bindings.
- All inference and embeddings use the existing `local-qwen` and `local-embeddings` aliases.
- Never run two processes against the same profile home at once.
- Run one generation workload at a time initially.
- New workload identities use neutral names; retain approved existing names and legal attribution.
- No hosted memory calls, Docker socket, automatic source writes, or volume resets.
- Separate knowledge, semantic memory, bounded notes, history, and caches.
- Keep secrets out of Git, process arguments, diagnostic output, and request logs.
- Preserve the pre-existing dirty update changes; stage only each task's files or hunks.

## Review focus

1. A valid store ID with a hostile filter must never widen repository access (Task 1).
2. A repeated startup or partially failed provisioning must not duplicate or rotate valid credentials (Tasks 2, 3, 7).
3. Deleting a semantic fact must not leave a mirrored note that still injects it (Task 4).
4. Cancellation, process failure, and restart must release locks without replaying work (Task 6).
5. Oversized or malformed output must not consume unbounded memory or expose raw prompts in logs (Tasks 1, 6).

## Delivery phases and file ownership

The approved scope spans three related subsystems. Implement them as separately
testable phases in this order, retaining a single integration plan because their
gateway credentials, profile identities, and startup order must agree.

| Phase | Tasks | Deliverable |
|---|---|---|
| Knowledge | 1–2 | Authenticated vector search through the gateway |
| Memory and skills | 3–5 | Restart-persistent, isolated local profiles |
| Execution and integration | 6–7 | Real A2A dispatch and repeatable `make up` |

Retrieval modules own store scope and search. The new `execution/` package owns
profile configuration, Mem0 integration, subprocess control, and A2A transport.
Provisioning scripts own database roles, credentials, and gateway registrations.
No module may silently invent another profile/store identifier.

Deterministic checks below use `.local/test-venv/bin/python`. Extend the existing
test installer when the execution package is introduced. Integration tests use
synthetic data with unique test namespaces and delete only those records.

## Task 1: Authenticated vector search adapter

**Files:** Create `retrieval/migrations/002_vector_registry.sql`,
`retrieval/src/local_ai_retrieval/vector_registry.py`,
`retrieval/src/local_ai_retrieval/vector_api.py`,
`retrieval/tests/test_vector_api.py`. Modify retrieval `config.py`, `db.py`,
and `service.py` to load the backend credential, run the additive migration,
and mount the routes. Extend `docs/architecture.md` with the adapter boundary.

**Interfaces:**

```python
@dataclass(frozen=True)
class StoreScope:
    store_id: str
    repository: str
    branch: str

def register_store(settings, repository: str, branch: str) -> StoreScope: ...
def get_store(settings, store_id: str) -> StoreScope | None: ...
def list_stores(settings, after: str | None, limit: int) -> list[StoreScope]: ...
def scoped_search(settings, scope: StoreScope, body: dict) -> dict: ...
```

- [ ] Write HTTP tests before routes exist. Use Starlette `TestClient`, injecting
  a fake registry/search dependency; prove responses and authorization, not
  internal call order. Include this contract assertion:

  ```python
  response = client.post('/v1/vector_stores/vs_test/search',
      headers={'Authorization': 'Bearer backend-test'},
      json={'query': 'policy', 'filters': {'type': 'eq',
            'key': 'repository', 'value': 'another-repository'}})
  assert response.status_code == 400
  assert search_calls == []
  ```

- [ ] Run `.local/test-venv/bin/python -m pytest retrieval/tests/test_vector_api.py -q`;
  expect missing adapter/route failures.
- [ ] Implement a unique `(repository, branch)` registry, opaque stable IDs,
  parameterized SQL, and backend-only bearer authentication using constant-time
  comparison. Protect listing/retrieval/search equally. Ordinary client keys fail.
- [ ] Implement `GET /v1/vector_stores`, `GET /v1/vector_stores/{id}`, and
  `POST /v1/vector_stores/{id}/search`. Default 6 results, maximum 20,
  query length 2–4000 characters, body maximum 64 KiB, context maximum 24000
  characters, embedding timeout 30 seconds, database statement timeout 5 seconds.
  Run blocking database/search work off the async event loop.
- [ ] Accept one string query; reject query arrays, rewrite requests, unknown
  rankers/operators, and unknown body fields explicitly. Support `eq` and `and`
  for repository, branch, and exact path; repository/branch must agree with the
  registry. Apply path filters in SQL before result limiting. Reject `or`.
  Implement stable ID cursor pagination with a bounded limit.
- [ ] Return the provider's search shape, validated against installed types:

  ```json
  {"object":"vector_store.search_results.page","search_query":"policy",
   "data":[{"file_id":"chunk_1","filename":"docs/policy.md","score":0.9,
     "attributes":{"repository":"fixture","branch":"main","chunk_id":1},
     "content":[{"type":"text","text":"Quoted source text"}]}],
   "has_more":false,"next_page":null}
  ```

  Include revision and line bounds in attributes. Verify score conversion from
  the existing distance calculation; do not label distance as similarity.
- [ ] Add cases for missing/bad auth (401), unknown ID (404), scope attack (400),
  malformed JSON/body (400/413), empty results (200), and backend timeout (504).
  Verify backend failures do not return source text or connection details.
- [ ] Run the adapter tests and all retrieval tests, then `git diff --check`.
  Commit the reviewed adapter changes as `feat: expose scoped vector search`.

## Task 2: Gateway registration and access enforcement

**Files:** Create `scripts/provision-workspace.py`,
`scripts/tests/test_workspace_provisioning.py`, `scripts/vector-smoke.py`.
Modify `compose.yaml`, `.env.example`, `scripts/bootstrap.sh`,
`scripts/provision-client-key.sh`, `litellm/config.yaml`, and `docs/architecture.md`.

**Interfaces:** `provision-workspace.py vectors` reads existing admin credentials
from ignored files, registers backend stores, and atomically writes
`.local/workspace-registry.json` containing IDs only. It accepts no secret CLI
arguments. Later modes are `profiles` and `routes` (Tasks 4 and 6).

- [ ] Capture the installed `/vector_store/new` request schema and permission
  model from container source into a non-secret test fixture. Verify registration
  uses provider `pg_vector`, internal retrieval base URL, and backend credentials.
  Do not copy assumed fields from examples for another version.
- [ ] Write the idempotency test around an in-memory HTTP fixture:

  ```python
  first = provision_vectors(gateway, registry, credentials)
  second = provision_vectors(gateway, registry, credentials)
  assert second == first
  assert gateway.created_count == len(first)
  assert credentials.read_bytes() == original_credentials
  ```

  Define `provision_vectors(gateway, registry, credentials) -> dict[str, str]`
  in the new script. Make transport injectable for tests.
- [ ] Run `.local/test-venv/bin/python -m unittest discover -s scripts/tests`;
  confirm the new test fails before implementing provisioning.
- [ ] Implement GET-before-create reconciliation, deterministic workload names,
  explicit permissions, and 0600 atomic secret writes. Reconcile remote success
  after local-write failure on rerun. Use HTTP headers in memory with neutral UA
  `Platform Provisioning`; never shell-expand bearer tokens into curl arguments.
- [ ] Wire backend credentials into retrieval and gateway using ignored mounted
  files or supported credential references. Keep master credentials out of the
  execution service. Do not grant vector management operations to runtime keys.
- [ ] Implement `vector-smoke.py`: index the existing fixture, search through
  `/v1/vector_stores/{id}/search`, assert expected path/content and source metadata.
  Verify denial with a valid key lacking store access, direct ordinary-key denial,
  and failed attempts to override backend URL/provider/embedding selection.
- [ ] Run the provisioning tests and live vector smoke. If the installed gateway
  permits a scope bypass, fix access enforcement before enabling registration.
  Commit as `feat: register local knowledge stores`.

## Task 3: Pinned runtime and isolated memory storage

**Files:** Create `execution/Dockerfile`, `execution/pyproject.toml`,
`execution/requirements.lock`, `execution/src/workspace_runtime/__init__.py`,
`postgres/provision-memory.py`, `execution/tests/test_memory_storage.py`.
Modify `compose.yaml`, `scripts/bootstrap.sh`, `scripts/test.sh`,
`scripts/lint.sh`, and `.github/workflows/ci.yml` for the additional package.

**Interfaces:** PostgreSQL database `workspace_memory`; separate login roles
`memory_development`, `memory_review`, `memory_research`, each restricted to a
same-named schema. Per-profile config receives only its own connection secret.
Provisioning runs with an existing administrative connection, never with a
runtime credential. No privilege is added to `mcp_reader`.

- [ ] Write real database checks that each role can create/read its collection
  and cannot read another schema or connect to the knowledge database. Assert:

  ```python
  with pytest.raises(psycopg.errors.InsufficientPrivilege):
      review_connection.execute('SELECT * FROM memory_development.memories')
  ```

  Use disposable test schemas where possible and verify production grants
  read-only after provisioning. Existing default PUBLIC privileges must be
  explicitly accounted for before claiming isolation.
- [ ] Run the tests against an isolated test database and observe missing-role
  failures. Do not create test tables in existing knowledge schemas.
- [ ] Build from Hermes v2026.9.14. Resolve `mem0ai==2.1.0` plus the psycopg pool
  required by its pgvector backend, without `mem0ai[llms]` or broad extras.
  Generate a complete exact lock for the container's Python and architecture;
  run `pip check` and import the Hermes memory plugin in the resulting image.
  The candidate pin is accepted only after this compatibility check.
- [ ] Implement idempotent role/database/schema provisioning with quoted SQL
  identifiers and parameters for values. Preserve existing role passwords on
  rerun. Ensure the Mem0 pgvector implementation honors each connection's schema;
  test with an actual add/search before accepting schema isolation. Do not patch
  vendor source in the running container as a deployment mechanism.
- [ ] Configure extension ownership separately from restricted runtime roles.
  Disable Mem0 telemetry using the supported switch verified in the locked
  source. Confirm HTTP destinations during the memory integration test.
- [ ] Run storage tests, runtime dependency checks, and `git diff --check`.
  Commit as `feat: provision isolated persistent memory storage`.

## Task 4: Profile configuration and complete memory lifecycle

**Files:** Create `execution/src/workspace_runtime/profiles.py`,
`execution/src/workspace_runtime/memory.py`,
`execution/tests/test_profiles.py`, `execution/tests/test_memory_lifecycle.py`,
`scripts/memory-smoke.py`. Modify `scripts/configure-hermes.sh` and
`scripts/provision-workspace.py` (`profiles` mode).

**Interfaces:**

```python
PROFILES = ('development', 'review', 'research')
def profile_home(root: Path, profile: str) -> Path: ...
def build_profile_config(profile: str, secrets: dict[str, str]) -> dict: ...
def add_fact(profile: str, text: str) -> str: ...
def search_facts(profile: str, query: str) -> list[dict]: ...
def update_fact(profile: str, fact_id: str, text: str) -> None: ...
def delete_fact(profile: str, fact_id: str) -> None: ...
```

- [ ] Write tests rejecting unknown profiles and traversal before opening files.
  Add memory lifecycle checks with two independent profile sessions:

  ```python
  fact_id = add_fact('development', 'The fixture release colour is amber.')
  assert search_facts('development', 'fixture release colour')
  assert not search_facts('review', 'fixture release colour')
  update_fact('development', fact_id, 'The fixture release colour is violet.')
  delete_fact('development', fact_id)
  assert not search_facts('development', 'fixture release colour')
  ```

  Assert the matching synthetic fact is absent rather than requiring an empty
  collection when other memories exist. Repeat recall in a new process.
- [ ] Run the new tests and observe missing implementation failures.
- [ ] Generate homes beneath `.local/profiles/{profile}` with private modes,
  separate history databases, separate memory schemas, scoped gateway keys,
  and frozen user/profile identities. Preserve existing homes and edited settings.
  Mem0 uses `mode: oss`, the two local gateway aliases, and dimension 1024 only
  after a live dimension check. A mismatch fails readiness without rebuilding data.
- [ ] Connect the pinned Hermes plugin to this configuration. Exercise actual
  plugin add/search/update/delete tools, not only direct SDK calls. Persist notes
  with fact-ID provenance so update/delete can remove their exact mirrored entries;
  do not fuzzy-delete unrelated notes. Disable automatic mirroring unless the
  adapter can preserve this provenance. Old session history remains distinct.
- [ ] Set timeouts and bounded retries. Explicit writes fail on storage failure;
  ordinary execution receives a structured degraded-memory status. Test memory
  database failure and confirm there is no success acknowledgment for a lost write.
- [ ] Implement live smoke: save synthetic fact, restart runtime, recall in a
  fresh session, test cross-profile denial, update and delete, restart and verify
  no reinjection from notes. Restore service health after failure injection.
- [ ] Run profile and lifecycle tests plus `memory-smoke.py`. Commit as
  `feat: add scoped memory lifecycle to local profiles`.

## Task 5: Reviewed and repeatable skill installation

**Files:** Create `execution/skills/manifest.json`,
`execution/skills/LICENSE.superpowers`, reviewed skill directories under
`execution/skills/`, `scripts/install-workspace-skills.py`,
`scripts/tests/test_workspace_skills.py`, and `docs/workspace-skills.md`.

**Interfaces:** Manifest records source URL, commit, file hashes, license,
modifications, profile allowlist. `install-workspace-skills.py` installs only
reviewed local bundles into profile homes and writes a per-home provenance file.

- [ ] Fetch and inspect only the three selected skills and their referenced
  assets at commit `5bf4e78011075bcfc0dc295f0724994cd123ee71` of `obra/superpowers`.
  This is the upstream revision observed on 2026-09-21; do not replace it with main.
- [ ] Write an installer test for edited content and traversal:

  ```python
  install_bundle(bundle, home)
  installed.write_text('local revision')
  result = install_bundle(bundle, home)
  assert installed.read_text() == 'local revision'
  assert result['status'] == 'local_changes'
  ```

  Define `install_bundle(bundle: Path, home: Path) -> dict[str, str]`. Reject
  escaping paths and symlinks before writing any installed file.
- [ ] Run `.local/test-venv/bin/python -m unittest discover -s scripts/tests`;
  confirm the installer tests fail before implementation.
- [ ] Retain MIT attribution and record exact changes to tool instructions.
  Install verification and diagnosis in all applicable profiles; install coding
  workflow only in development. Never claim shell/test execution where disabled.
  Installation is offline from reviewed bundles, atomic, and hash-checked.
- [ ] Verify skills are discoverable through the real pinned Hermes runtime.
  Reinstall unchanged bundles and verify no changes; modify a test copy and
  verify preservation. Commit as `feat: bundle reviewed workspace skills`.

## Task 6: Bounded execution service and A2A transport

**Files:** Create `execution/src/workspace_runtime/service.py`, `protocol.py`,
`runner.py`, `task_store.py`, `execution/tests/test_protocol.py`,
`test_runner.py`, `test_task_store.py`. Modify execution package dependencies,
its Dockerfile, `compose.yaml`, and provisioning `routes` mode.

**Interfaces:**

```python
@dataclass(frozen=True)
class TaskRequest:
    task_id: str
    profile: str
    text: str

@dataclass(frozen=True)
class TaskResult:
    task_id: str
    state: str
    text: str
    memory_status: str

async def execute_task(request: TaskRequest) -> TaskResult: ...
async def cancel_task(task_id: str) -> bool: ...
```

- [ ] Write protocol tests for text message/send, card discovery, tasks/get,
  cancellation, malformed input, unsupported parts/methods, and missing auth.
  Use A2A 0.3 JSON-RPC shapes verified against the installed gateway SDK. Pin the
  same compatible SDK version if used; never advertise unsupported capabilities.
- [ ] Write runner tests using a real tiny Python subprocess fixture that can
  succeed, hang, emit excess output, spawn a child, or fail. Assert:

  ```python
  result = await execute_task(hanging_request)
  assert result.state == 'failed'
  assert not process_group_exists(hanging_request.task_id)
  following = await execute_task(ordinary_request)
  assert following.state == 'completed'
  ```

  Define the process fixture helpers in `test_runner.py`, with fixture-specific
  short deadlines; never kill unrelated host processes.
- [ ] Run `.local/test-venv/bin/python -m pytest execution/tests -q`;
  expect missing execution implementation failures.
- [ ] Implement one active execution slot, FIFO queue of 3, queue wait 10 seconds,
  execution deadline 45 seconds, maximum input 16000 characters, captured output
  maximum 256 KiB, response maximum 24000 characters. Gateway forwarding was
  observed to have a 60-second timeout; keep the complete backend budget below it.
  Return busy rather than accepting unbounded work. Explicitly report timeouts;
  slow local tasks may require a separately designed asynchronous mode later.
- [ ] Run one service worker with per-profile filesystem locks. Launch subprocesses
  without a shell, separate process groups, sanitized environments, and task text
  via stdin/private input files rather than argv. Inspect the pinned runtime's
  supported structured input/output interface before implementing its adapter.
  Kill the group on cancellation/deadline; reap it before releasing the slot.
- [ ] Run development with writable scratch only, source read-only. Review and
  research expose only their read-only tools plus their private memory operations.
  Each child sees only its profile credentials/home; verify environment and mount
  isolation, not just prompts. Avoid mounting all profile secrets into children.
- [ ] Persist task state atomically in `.local/execution/state.db`. Transition
  queued → working → completed/failed/canceled; interrupted queued/working records
  become failed on startup. Never replay them. Scope task retrieval/cancellation
  to the caller's authorized profile, and reject duplicate message IDs without
  executing twice. Raw subprocess diagnostic output is not a public response.
- [ ] Implement cards and dispatch at `/profiles/{profile}` with backend-only
  authentication. Register the actual service URLs in LiteLLM and verify gateway
  object permissions. The private backend is not a way around gateway grants.
- [ ] Run protocol, runner, task-state tests, then a real request per profile
  through LiteLLM, including a denied caller. Commit as
  `feat: expose bounded profile execution through the gateway`.

## Task 7: Supported startup, acceptance, and handoff

**Files:** Modify `scripts/up.sh`, `scripts/bootstrap.sh`, `scripts/smoke-test.sh`,
`scripts/status.sh`, `Makefile`, `compose.yaml`, `README.md`,
`docs/architecture.md`, `docs/troubleshooting.md`, and CI workflow.
Create `scripts/workspace-smoke.py`, `scripts/tests/test_workspace_startup.py`,
and `docs/workspace-memory.md`.

**Interfaces:** Add `make vector-smoke`, `make memory-smoke`, and
`make workspace-smoke`; `make up` includes readiness and bounded acceptance.
Keep the existing one-shot Hermes target working.

- [ ] Add startup tests with fake commands to capture ordering and failure exit:

  ```python
  assert events.index('gateway-ready') < events.index('profile-credentials')
  assert events.index('profile-credentials') < events.index('execution-ready')
  assert events.index('execution-ready') < events.index('register-routes')
  assert failed_stage_exit_code != 0
  ```

- [ ] Run startup tests before changing `up.sh`, then implement staged startup:
  existing bootstrap → database/gateway → memory provisioning → scoped keys and
  profiles → retrieval/execution → registrations → existing and new smoke checks.
  Do not let missing generated credentials block the initial gateway startup.
- [ ] Record actual backend/profile readiness in status output, with explicit
  pending/degraded states. Smoke tests have total deadlines and useful failure
  diagnostics containing metadata only. No production prompts/memories in logs.
- [ ] Run `make up` twice. Compare registry/profile IDs and credential hashes
  internally; print only pass/fail. Confirm existing source chunks and gateway
  records survive. Stop/restart the execution service and verify interrupted
  tasks are not replayed. Verify network destinations remain local for inference,
  embeddings, and memory, and that configured telemetry is disabled.
- [ ] Run `make lint`, `make test`, `make smoke-test`, `make hermes-smoke`,
  `make vector-smoke`, `make memory-smoke`, and `make workspace-smoke`.
  Record exact runtime pins and verification date. If a check is unavailable,
  report it explicitly rather than treating the phase as complete.
- [ ] Document commands, three profile purposes, source citations, memory
  add/update/delete, retained session history, timeout/queue limits, supported
  text inputs, recovery, and rollback without volume deletion. Keep unsupported
  PDF/upload/streaming features clearly outside verified behavior.
- [ ] Review the complete change against all nine spec acceptance criteria and
  the five review-focus cases. Commit reviewed integration changes as
  `feat: integrate persistent workspace services into startup`.

## Plan self-review and execution choice

Spec coverage: knowledge Task 1–2; profiles Task 4/6; memory Task 3/4;
skills Task 5; startup and recovery Task 7; verification distributed across all
tasks with final acceptance in Task 7. Interfaces and profile identifiers are
shared explicitly. Public schema and runtime dependency assumptions are tested
before dependent changes are enabled.

Recommended execution: direct implementation in the current session, in task
order, followed by independent final review. The components share local gateway
contracts and Compose configuration, so concurrent edits would add coordination
cost. Delegated implementation with separate review per task remains an option.

Implementation begins after the user reviews this plan and selects the method.
