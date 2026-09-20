# Dependency update — 2026-09-20

Versions checked against upstream release metadata, PyPI, crates.io, and container
registries on 2026-09-20. Stable published artifacts are selected; prereleases are
excluded. The July capability report remains historical evidence.

| Component | Previous | Selected | Source |
| --- | --- | --- | --- |
| Docker Desktop | 4.88.1 | 4.91.0 (installed) | [Release notes](https://docs.docker.com/desktop/release-notes/) |
| LiteLLM | 1.93.0 | 1.102.0 | [Package metadata](https://pypi.org/pypi/litellm/json); ARM64 registry manifest verified |
| pgvector | 0.8.5 | 0.8.6, PostgreSQL 17 Bookworm | [Upstream tags](https://github.com/pgvector/pgvector/tags) |
| Redis | 8.8.0 | 8.10.1 Alpine | [Published images](https://hub.docker.com/_/redis/tags) |
| Prometheus | 3.13.1 | 3.14.0 | [Release](https://github.com/prometheus/prometheus/releases/tag/v3.14.0) |
| Grafana | 13.1.0 | 13.2.2 | [Release](https://github.com/grafana/grafana/releases/tag/v13.2.2) |
| Loki | 3.7.3 | 3.7.8 | [Release](https://github.com/grafana/loki/releases/tag/v3.7.8) |
| Alloy | 1.17.1 | 1.19.2 | [Release](https://github.com/grafana/alloy/releases/tag/v1.19.2) |
| PostgreSQL exporter | 0.20.1 | 0.20.1, unchanged | [Release](https://github.com/prometheus-community/postgres_exporter/releases/tag/v0.20.1) |
| Redis exporter | 1.87.0 | 1.91.1 | [Release](https://github.com/oliver006/redis_exporter/releases/tag/v1.91.1) |
| GitHub MCP server | 1.6.0 | 1.12.2 | [Release](https://github.com/github/github-mcp-server/releases/tag/v1.12.2) |
| Hermes | 2026.7.7.2 | 2026.9.14 (package 0.21.3) | [Published images](https://hub.docker.com/r/nousresearch/hermes-agent/tags) |
| Python base image | 3.14.6 | 3.14.7, slim Bookworm | [Published images](https://hub.docker.com/_/python/tags) |
| Rust build image | 1.97.1 | 1.98.1, Bookworm | [Published images](https://hub.docker.com/_/rust/tags) |
| setuptools | 80.10.2 | 84.0.0 | [PyPI](https://pypi.org/project/setuptools/84.0.0/) |
| FastAPI | 0.139.2 | 0.141.1 | [PyPI](https://pypi.org/project/fastapi/0.141.1/) |
| MCP SDK | 1.28.1 | 2.2.0 | [Release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.2.0) |
| prometheus-client | 0.25.0 | 0.26.0 | [PyPI](https://pypi.org/project/prometheus-client/0.26.0/) |
| psycopg | 3.3.4 | 3.3.6 | [PyPI](https://pypi.org/project/psycopg/3.3.6/) |
| Pydantic | 2.13.4 | 2.13.5 | [PyPI](https://pypi.org/project/pydantic/2.13.5/) |
| Uvicorn | 0.51.0 | 0.53.0 | [PyPI](https://pypi.org/project/uvicorn/0.53.0/) |
| Tokio | 1.53.0 | 1.53.1 | [Registry](https://crates.io/crates/tokio/1.53.1) |
| serde_json | 1.0.150 | 1.0.151 | [Registry](https://crates.io/crates/serde_json/1.0.151) |
| Checkout workflow | 4 | 7.0.1 | [Release](https://github.com/actions/checkout/releases/tag/v7.0.1) |
| Python setup workflow | 5 | 7.0.0 | [Release](https://github.com/actions/setup-python/releases/tag/v7.0.0) |

HTTPX 0.28.1, pathspec 1.1.1, pytest 9.1.1, Axum 0.8.9, and serde 1.0.229
were already current according to their package registries. Cargo's transitive
lockfile dependencies were refreshed for Rust 1.98.1. CI uses the same Python
and Rust versions as the build images. Both workflow actions use Node 24;
remote workflow execution is not established by local tests.

## Compatibility decisions

- LiteLLM's latest GitHub release pointer still returned 1.101.0, but both PyPI
  and the published multiarchitecture container confirmed 1.102.0.
- Redis 8.10.2 has an upstream release but no `8.10.2-alpine` image at verification
  time. The selected 8.10.1 image was verified for ARM64.
- PostgreSQL stays on major 17. Switching an existing data volume to major 18
  requires a separate data migration. Updating the pgvector image alone does
  not upgrade an extension in an existing database: inspect `pg_extension` and
  plan `ALTER EXTENSION vector UPDATE` for each affected database.
- Existing inference and embedding artifact names are retained and refreshed by
  `make up`. Changing the embedding family requires rebuilding stored vectors.
- MCP SDK 2 removes `FastMCP` and constructor transport settings. Both services
  now use `MCPServer`, with path, stateless mode, JSON mode, and DNS rebinding
  protection passed to the mounted HTTP app. Lifespan session management stays
  active. See the [upstream migration guide](https://github.com/modelcontextprotocol/python-sdk/blob/v2.2.0/docs/migration.md).
- HTTP transport tests cover tool discovery, execution, secret-file denial, and
  rejection of an untrusted Host header using the legacy protocol supported by
  the gateway. Application dependencies are resolved together, followed by
  `pip check`, so sequential editable installs cannot hide conflicting pins.
- Offline remains the default. Optional client and connected-service image
  manifests were checked for ARM64; that does not establish end-to-end operation.

## Verification

- `make lint`: passed, including ShellCheck, Compose configuration, and secret-pattern checks.
- `make test`: passed, with 7 retrieval tests, 5 offline-tool tests, both Rust test
  cases, and `pip check`. Rust tests enforce the refreshed lockfile with `--locked`.
- `make up` and a subsequent `make smoke-test`: passed after the Desktop update,
  including the full smoke script:
  authentication, localhost bindings, 1024-dimensional embeddings, generation,
  Responses function-call round trip and streaming, Redis response cache hit,
  incremental ingestion and vector search, actual MCP calls, all configured
  metrics targets, and absence of synthetic prompt text in collected logs.
- Runtime confirmed: LiteLLM 1.102.0; PostgreSQL 17.11 with vector extension
  0.8.6; Docker Desktop 4.91.0; Engine 29.8.0; Compose 5.5.1. The bundled
  inference client remains 1.2.6 and server 1.2.8 (upstream's current server release).
- LiteLLM image digest:
  `sha256:32cfd7a427f6470033b4dcc9e75dfbea2aa32dbc298b0b511a40e962ce818362`.
- GitHub MCP image was pulled and its isolated `--version` command confirmed
  1.12.2. Authenticated connected-mode operations were not tested.
- All three Hermes configuration templates passed the pinned image's own
  structural validator. Newly generated configurations declare schema 44,
  matching that image, instead of triggering its legacy-schema warning.
- `make hermes-smoke`: passed through the local gateway using the updated
  container and schema 44. This establishes one-shot chat compatibility, not a
  complete coding workflow.
- Hosted CI and interactive editor workflows were not executed.

### Restart recovery observed during this update

A direct Compose restart after the Desktop upgrade did not restore the custom
inference context. The runtime used 128,000 tokens, returned `503 Loading model`,
and subsequently logged a Metal out-of-memory error. One pre-upgrade inference
process also remained orphaned. The affected workloads were unloaded, the exact
orphaned process was terminated, and the supported `make up` entry point restored
65,536 tokens. The complete smoke script then passed. After a Desktop restart,
use `make up` to restore runtime configuration; container health alone does not
prove inference readiness. No artifact or database volume was deleted.

### Monitoring readiness follow-up — 2026-09-21

A subsequent startup reached the MCP checks and exited before the monitoring
success message. Historical Prometheus samples showed retrieval temporarily down
after its restart; the same endpoints and all six targets were healthy when
inspected. The former one-shot check could fail before the next 15-second scrape
without identifying the failing condition.

The smoke test now waits up to 60 seconds for monitoring, rejects an empty target
list, and reports the failing endpoint or target's last scrape error on timeout.
Four loopback HTTP regression cases cover recovery, persistent failure, empty
results, and a missing retrieval metric. `make lint`, `make test`, `make up`, and
`make smoke-test` all passed after this change. See
[monitoring startup troubleshooting](troubleshooting.md#startup-reaches-mcp-checks-but-monitoring-is-not-ready).
