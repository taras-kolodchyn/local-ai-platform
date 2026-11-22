# Platform operations

## Start, inspect and stop

| Command | Effect |
| --- | --- |
| `make bootstrap` | Create or update ignored local configuration and secrets, and render the LiteLLM template. Use this before the first startup if you need to adjust the model context. |
| `make up` | Prepare model artifacts, check the host, build and start core services, provision keys and Moyai skills, start Moyai and run the endpoint smoke checks. |
| `make status FORMAT=json` | Read both projects' container state, configured DMR references, artifact metadata, residency and observable host memory. This works before bootstrap and reports unavailable inventories explicitly. |
| `make logs` / `make moyai-status` | Follow core service logs or show the Moyai project's containers. |
| `make down` | Stop Moyai, stop core services across profiles and unload the two configured DMR models. Persistent volumes and downloaded models survive. |
| `make restart` | Run the complete stop/start cycle. |

Run lifecycle commands from the checkout that owns the deployment. The default
project names (`local-ai-platform` and `local-ai-moyai`) and host ports are shared
across worktrees on the same Mac. The bind-mounted generated configuration and
secrets belong to the checkout that started the services.
[Core Compose project](../compose.yaml) · [Moyai project](../compose.moyai.yaml) ·
[Lifecycle targets](../Makefile)

### Repeated Compose builds

Startup checks the local image ID and the contents, paths and permissions of
each image’s build inputs. The Dockerfile and its effective `.dockerignore`
are included. Only services with changed inputs or a missing or replaced image
go through `docker compose build`. Both projects still run `compose up --wait`
and the full smoke checks. Runtime settings in `.env` continue to take effect
through bootstrap and Compose reconciliation.

The first run populates `.local/compose-builds/` after a successful build.
Removing these receipts forces a build on the next startup. Keep new build
sources in the corresponding Docker context allowlist. More complex ignore
patterns or additional build options fall back to a regular Compose build.
Verified on Apple Silicon on 2026-11-21 with Docker Compose v5.7.1 and
Buildx v0.38.1.
[Docker build contexts and Dockerfile-specific ignore files](https://docs.docker.com/build/concepts/context/)

### MCP database role on startup

After Compose reconciles PostgreSQL, `make up` opens a fresh SCRAM connection to
`local_ai` as `mcp_reader` using `MCP_POSTGRES_PASSWORD` from `.env`. It checks
the login's role attributes, read-only default and five-second statement timeout,
plus its direct grants for database access, schema usage, existing public
tables and future tables created by `localai`. When those checks pass, Compose
skips the one-shot `mcp-provisioner` and still waits for the core services.

To rotate the MCP password, update `MCP_POSTGRES_PASSWORD` in `.env` and run
`make up`. A failed check starts the provisioner, which reconciles the role and
managed grants in one transaction. MCP waits for the provisioner to finish, and
startup verifies the new login and grants again. The same check runs after a
database restore, including a logical restore that omitted grants. A restore
that preserved the password and all managed privileges can skip provisioning.
Verified on Apple Silicon on 2026-11-22 with PostgreSQL 17.12 and Docker
Compose v5.7.1. [PostgreSQL connection authentication](https://www.postgresql.org/docs/17/libpq-connect.html#LIBPQ-CONNECT-REQUIRE-AUTH) ·
[Default privileges](https://www.postgresql.org/docs/17/catalog-pg-default-acl.html) ·
[Compose merge rules](https://docs.docker.com/reference/compose-file/merge/)

### Temporal data volume on startup

On a repeated `make up`, Moyai checks that the running Temporal container
mounts the configured named volume at `/data` and that the directory has UID
1000 and GID 1000. When both match, Compose skips `moyai-volume-init`.
A new volume, a different owner, or an unavailable container or probe uses the
normal startup dependency. The initializer must succeed before Temporal starts.
Both paths wait for service health and run the usual smoke checks.
Verified on Apple Silicon on 2026-11-22 with Temporal 1.10.1 and Docker Compose
v5.7.1. [Moyai services and volumes](../compose.moyai.yaml) ·
[Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/)

## Resolved models and status

Set `QWEN_MODEL`, `EMBEDDING_MODEL` and `QWEN_CONTEXT_SIZE` in the ignored
`.env`. Missing fields take their defaults from `.env.example`. On startup,
bootstrap renders the English descriptions and DMR bindings into
`.local/litellm/config.yaml` and the model preparation script records the
embedding artifact identity for retrieval. A service-specific environment file
records the SHA-256 of the rendered YAML. Compose picks up changes to model
descriptions and other gateway settings in the same step that starts LiteLLM.
Repeated `make up` keeps healthy LiteLLM and retrieval containers running when
their inputs match. Retrieval reads its client key per request. Startup checks
its mounted keys after provisioning and rebinds them if their contents differ.
Every `make up` still runs the route and dashboard smoke checks.
[Compose startup behavior](https://docs.docker.com/reference/cli/docker/compose/up/) ·
[Model resolver](../scripts/model-config.py) · [Version and alias matrix](capability-matrix.md)

In status output, requested context comes from platform configuration, artifact
context comes from inspectable model metadata and process RSS comes from macOS.
Effective runtime context and per-model Metal allocation are reported as
unavailable. Use `docker model ps` to inspect resident models and
`docker model list` for downloaded artifacts. [DMR configuration](https://docs.docker.com/ai/model-runner/configuration/) ·
[Status implementation](../scripts/model-status.py)

## Hybrid retrieval

`make index REPO=/absolute/path` updates the searchable repository/branch
index, reusing compatible embeddings for unchanged chunks. `make reindex`
with the same argument rebuilds that index. Use `make repository-add` for a
supported checkout when a Moyai task needs an immutable source snapshot and a
pinned indexed generation. [Repository registration and supported inputs](repository-workflow.md)

The query combines bounded cosine candidates from pgvector with full-text
matches on paths, symbols and content. Both channels apply the same scope
restrictions before reciprocal rank fusion (RRF). A rank `r` contributes
`1/(60+r)` in each channel where the chunk appears. The vector-store
`score_threshold` applies to the fused score multiplied by `61/2` and clamped
to `[0,1]`. A first-place match in just one channel scores 0.5. Results also
carry the raw RRF and cosine similarity as separate attributes.
[Hybrid query](../retrieval/src/local_ai_retrieval/db.py) ·
[PostgreSQL 17 text search](https://www.postgresql.org/docs/17/textsearch-controls.html) ·
[pgvector hybrid search](https://github.com/pgvector/pgvector#hybrid-search)

Moyai's context bridge requests automatic ranking within the attached
generation. The backend considers up to 48 hybrid results, optionally reranks
implementation excerpts with MiniLM, then packs the selected chunks into the
configured source budget (at most 24,000 characters). The returned score still
describes the hybrid rank. The context bridge checks the scope and source
coordinates before returning excerpts to the task.
[Candidate selection](../retrieval/src/local_ai_retrieval/moyai_search.py) ·
[Context bridge](../integrations/moyai/context_bridge.py)

## Verification

```sh
make lint
make test
make smoke-test
```

The live smoke suite requires the running stack. `make vector-smoke` repeats
the scoped vector-store citation and access checks. `make moyai-smoke RESTART=1`
exercises native workspace actions, tests, artifact delivery and restart
persistence. [Endpoint checks](../scripts/smoke-test.sh) · [Moyai runbook](moyai-local.md)

`make verify` runs lint, tests, startup, JSON status, endpoint smoke and Moyai
acceptance with a restart. Supply `REPO=/absolute/path/to/compatible-crate` to
add the repository-specific acceptance phase. The finalizer attempts shutdown
even after a failure and independently checks for remaining containers from
either project and for configured models still resident in DMR. Each run writes
private phase logs and a `result.json` in its own directory under
`.local/verification/`. [Verification runner](../scripts/verify.py)

## Upgrading installations with retired workspace agents

Moyai is the only agent started by `make up`. The `execution/` tree contains the
earlier workspace runtime and its database-isolation tests, which still run in CI.

During startup, reconciliation stops old development, review and research
containers and removes their platform-owned LiteLLM agent registrations. Moyai
uses its local UI integration. If an old installation still needs permanent
removal of those profiles' data, the explicit `--purge-data` migration revokes
their verified keys and removes the old state volumes, profile files and
`workspace_memory` database and roles. Review its scope before invoking it
against a running platform. [Migration implementation](../scripts/reconcile-agents.py)
