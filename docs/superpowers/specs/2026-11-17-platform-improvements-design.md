# Local platform improvements

The user selected improvements 1, 3, 5 and 6 on 2026-11-17: consistent model
configuration, one complete verification command, hybrid code search and a more
informative status. They also requested removing the configured legacy agents
from LiteLLM and keeping local Moyai as the single active agent. They subsequently
explicitly authorized deleting the retired agents' data as well. Existing local
English model descriptions are part of this work.

## Intended behavior

- `.env` remains the source of active model references and context. A validated
  resolver generates LiteLLM's effective model configuration and supplies the
  status command. Clients retain `local-qwen` and `local-embeddings`. Descriptions
  and all LiteLLM comments are English. Model identity, context and metadata must
  never silently disagree. Model replacement is outside this change.
- `make verify` runs lint, required unit/DB/Rust tests, supported startup,
  endpoint smoke and real Moyai file/shell/test/artifact acceptance. It always
  attempts supported shutdown and checks that owned containers and model
  allocations are gone. Failures remain failures even when cleanup succeeds.
- Retrieval combines bounded pgvector cosine candidates with PostgreSQL
  full-text candidates over source, symbols and paths using rank fusion. Both
  candidate sets obey identical repository/branch/path scopes. Existing
  citations, context limits and read-only transports remain intact. Tests use a
  real disposable PostgreSQL instance and deterministic vectors.
- `make status` reports configured aliases, actual references, English
  descriptions, configured and observed context, artifact digest/format/size,
  resident status and measured memory when available. Artifact size, CPU RSS,
  total host memory and Metal allocations must not be presented as equivalent.
  Status is read-only and works with the stack stopped. Optional JSON output
  supports verification and tooling. Unavailable measurements are explicit.
- Default startup stops launching and recreating the three legacy workspace
  profiles, reconciles away their owned LiteLLM A2A registrations and stale
  grants. An explicitly invoked one-time purge deletes their private profile
  memory, directories, volumes and dedicated keys; ordinary startup is not a
  data deletion command.
  Keep retrieval, scoped knowledge access and Moyai's restricted model key. Do
  not manufacture an A2A facade for Moyai: its current integration is a local UI
  link and inference through LiteLLM; further agent management is future work.

## Approaches

Use existing environment settings plus generated LiteLLM configuration instead
of introducing a second independent configuration system. Resolve metadata from
known model definitions or explicit descriptions; do not retain a previous
model's label when its runtime reference changes.

Use built-in PostgreSQL full-text search and pgvector rather than another search
service or reranker model. This preserves offline operation and resource usage.
Rank fusion avoids comparing unrelated cosine and keyword score scales.

Retire owned legacy registrations and default startup. The user confirmed only
Moyai should remain active and then explicitly allowed deleting legacy data.
Use a separate, narrowly scoped purge rather than making every startup delete
data or deleting whole Docker projects/volumes indiscriminately.

## Constraints and acceptance

- `make up` stays the supported entry point, default offline and loopback-only.
- Inference stays in Docker Model Runner's native macOS boundary.
- No Docker socket, host-home mount, external writable MCP or cloud provider.
- No committed `.env`, generated keys, source indexes, model data or requests.
- Current model artifacts and existing embedding dimension 1024 remain intact.
- `make verify` must leave the platform off on both success and failure; actual
  final container/model inventories are required evidence.
- Only explicitly authorized legacy profile data may be deleted. Model upgrades,
  backups and model-quality comparisons were not selected.

References checked 2026-11-17: Docker Model Runner 1.2.9 CLI, LiteLLM 1.109.1,
[PostgreSQL text search](https://www.postgresql.org/docs/17/textsearch-controls.html),
[pgvector hybrid search](https://github.com/pgvector/pgvector#hybrid-search).
