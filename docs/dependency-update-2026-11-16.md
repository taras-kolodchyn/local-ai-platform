# Dependency update — 2026-11-16

Verification date: 2026-11-16, Apple Silicon Mac with 128 GiB unified memory.
The initial state had no Compose containers and Docker Model Runner disabled.
Docker Desktop was already 4.99.0 (Engine 29.9.3); no Desktop upgrade was needed.
Existing data volumes were preserved.

## Selected versions

Stable package metadata and ARM64 image availability were checked against the
linked upstream registries. PostgreSQL remains on major 17 to preserve the data
volume format; inference aliases and the 1024-dimensional embedding family are
unchanged.

| Component | Previous | Selected | Official source |
| --- | --- | --- | --- |
| LiteLLM | 1.102.0 | 1.109.1 | [PyPI](https://pypi.org/project/litellm/1.109.1/) |
| pgvector image | 0.8.6-pg17-bookworm | 0.8.7-pg17-bookworm | [Images](https://hub.docker.com/r/pgvector/pgvector/tags) |
| Redis | 8.10.1-alpine | 8.10.3-alpine | [Images](https://hub.docker.com/_/redis/tags) |
| Prometheus | 3.14.0 | 3.16.0 | [Release](https://github.com/prometheus/prometheus/releases/tag/v3.16.0) |
| Grafana | 13.2.2 | 13.3.1 | [Release](https://github.com/grafana/grafana/releases/tag/v13.3.1) |
| Loki | 3.7.8 | 3.7.10 | [Release](https://github.com/grafana/loki/releases/tag/v3.7.10) |
| Alloy | 1.19.2 | 1.22.0 | [Release](https://github.com/grafana/alloy/releases/tag/v1.22.0) |
| Redis exporter | 1.91.1 | 1.97.0 | [Release](https://github.com/oliver006/redis_exporter/releases/tag/v1.97.0) |
| GitHub MCP | 1.12.2 | 1.14.0 | [Release](https://github.com/github/github-mcp-server/releases/tag/v1.14.0) |
| Python base / CI | 3.14.7 | 3.14.10 | [Images](https://hub.docker.com/_/python/tags) |
| Rust build / CI | 1.98.1 | 1.100.0 | [Images](https://hub.docker.com/_/rust/tags) |
| setuptools | 84.0.0 | 84.2.1 | [PyPI](https://pypi.org/project/setuptools/84.2.1/) |
| FastAPI | 0.141.1 | 0.163.1 | [PyPI](https://pypi.org/project/fastapi/0.163.1/) |
| MCP SDK | 2.2.0 | 2.3.0 | [PyPI](https://pypi.org/project/mcp/2.3.0/) |
| psycopg | 3.3.6 | 3.3.9 | [PyPI](https://pypi.org/project/psycopg/3.3.9/) |
| Pydantic | 2.13.5 | 2.14.3 | [PyPI](https://pypi.org/project/pydantic/2.14.3/) |
| Starlette | 1.6.0 | 1.10.0 | [PyPI](https://pypi.org/project/starlette/1.10.0/) |
| Uvicorn | 0.53.0 | 0.54.0 | [PyPI](https://pypi.org/project/uvicorn/0.54.0/) |
| Hermes image | v2026.9.14 | v0.23.1, digest pinned | [Release](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.1) |
| Mem0 provider | built-in | external 1.3.0, commit pinned | [Source](https://github.com/mem0ai/mem0/tree/43849c6e9d4d773b83a8144b6373aedc67241a5d/integrations/hermes-plugin-mem0) |
| certifi runtime | 2026.5.20 | 2026.10.19 | [PyPI](https://pypi.org/project/certifi/2026.10.19/) |
| Pillow runtime | 12.3.0 | 13.0.0 | [PyPI](https://pypi.org/project/pillow/13.0.0/) |
| PyJWT runtime | 2.13.0 | 2.15.1 | [PyPI](https://pypi.org/project/PyJWT/2.15.1/) |
| Mem0 runtime | 2.1.0 | 2.2.1 | [PyPI](https://pypi.org/project/mem0ai/2.2.1/) |
| pytest | 9.1.1 | 9.2.0 | [PyPI](https://pypi.org/project/pytest/9.2.0/) |
| Tokio | 1.53.1 | 1.54.1 | [crates.io](https://crates.io/crates/tokio/1.54.1) |
| serde_json | 1.0.151 | 1.0.154 | [crates.io](https://crates.io/crates/serde_json/1.0.154) |
| setup-python action | 7.0.0 | 7.1.0 | [Release](https://github.com/actions/setup-python/releases/tag/v7.1.0) |

HTTPX 0.28.1, pathspec 1.1.1, prometheus-client 0.26.0, Axum 0.8.9,
serde 1.0.229, postgres-exporter 0.20.1, and checkout 7.0.1 were retained.
Cargo's transitive dependencies were refreshed with Rust 1.100.0.

## Compatibility decisions

- The initial update retained Hermes v2026.9.14 because the new release removed
  the built-in Mem0 module. The authorized follow-up migrated both together:
  the v0.23.1 image is digest-pinned, and the official catalog's standalone
  Mem0 provider 1.3.0 is baked into the immutable provider directory at image
  build time. Existing `memory.provider: mem0`, OSS configuration and identities
  remain valid; no lazy installation or cloud fallback was introduced.
- Both the one-shot Hermes CLI and all execution profiles use the same patched
  `workspace-runtime` image. The CLI retains upstream's entrypoint dispatcher.
  Newly generated configurations declare schema 53; existing v44 profile
  configuration remains readable and was verified without overwriting settings.
- Hermes v0.23.1 deliberately overrides browser-harness 0.1.13's old
  `pillow==12.3.0` requirement with Pillow 13.0.0 in its
  [upstream lock policy](https://github.com/NousResearch/hermes-agent/blob/v0.23.1/pyproject.toml).
  Its image's synthetic 0.0.0 distribution metadata also contains optional URL
  requirements rejected by pip's parser. Runtime dependency validation therefore
  uses pinned uv 0.13.8. `execution/check-dependencies.py` accepts only the exact
  single browser-harness/Pillow mismatch when the image's own `uv.lock` declares
  and locks Pillow 13.0.0; missing/truncated reports, other callers, versions,
  conflicts and command failures remain errors. Nine focused tests cover this.
  This is not reported as a clean raw `pip check` result.
- GitHub MCP stays within major 1. Version 1.14.0's isolated `--version`
  command passed. Connected authentication was not exercised; the connected
  profile remains opt-in.
- Host Python is 3.15.2, beyond the project's declared `<3.15` constraint.
  `make test` now selects Python 3.14–3.11, supports an explicit `PYTHON`, and
  recreates an incompatible generated test environment. Python 3.14.10 and
  ShellCheck 0.11.0 were installed for local verification. No system Python
  symlinks were changed.
- Updating a pgvector image does not migrate an existing extension. Check
  `pg_extension.extversion` per database before claiming the SQL extension
  itself was updated.

## Verification

- `make lint`: passed, including ShellCheck 0.11.0 and Compose validation.
- `make test`: 22 retrieval, 5 offline MCP, 7 execution, and 18 script tests
  passed. One external-database test was skipped because `MEMORY_TEST_DSN`
  was not supplied. The launcher skipped Rust because host `cargo` is absent;
  both Rust tests passed separately in `rust:1.100.0-bookworm` with `--locked`.
- `make up`: passed after the startup corrections below, including core
  inference/cache/retrieval/MCP/monitoring smoke checks, vector-store scope
  denial, persistent memory add/recall/isolation/update/delete across fresh
  processes, all three gateway-dispatched profiles, and execution authorization.
- A separate final `make smoke-test` passed after startup, using the scoped
  client key. The former editor integration and `make hermes-smoke` passed through
  the same local gateway with that scoped credential.
- Final status: 14 long-running services running; every configured container
  health check healthy and all configured Prometheus scrape targets healthy.
  Host listeners verified loopback-only. Default offline mode retained.
- Confirmed runtime versions: LiteLLM 1.109.1; PostgreSQL 17.12; pgvector
  extension 0.8.7; Redis 8.10.3; Docker Model Runner client/server 1.2.9;
  Hermes image v0.23.1 with Mem0 SDK 2.2.1 and provider 1.3.0.
- VS Code 1.146.0 / official Codex extension 26.1111.21308 / bundled Codex
  0.169.0-alpha.11.1: configuration/provider checks and an ephemeral local turn
  passed before the platform retired this integration. Interactive autonomous editing was not tested.
- No hosted CI run or authenticated connected GitHub MCP check was performed.

## Startup credential correction

A real `make up` run reached vector search but failed cross-scope denial.
Bootstrap deliberately seeds the generated client-key file with the master key
until LiteLLM starts. The provisioning script previously preserved any key
accepted by `/v1/models`, including that administrator placeholder. It now
replaces the placeholder with a scoped virtual key. A shell regression test
reproduced the failure before the change and passed afterward; the live vector
search check then confirmed that the ordinary client key receives HTTP 403.
Generated client environment files and Codex credentials were refreshed.

Repeated profile provisioning also replaced unchanged credential-verifier files
that were already mounted into running containers. Docker Desktop then returned
`FileNotFoundError` inside the backend, causing execution to fail with HTTP 403
(and the gateway to report 500). `private_write` now preserves unchanged file
inodes while enforcing mode 0600. Workspace startup recreates its three backend
containers to refresh mounts when generated configuration changes; named state
volumes remain intact. A regression test failed on inode replacement before the
fix and passed afterward. After recreating backends, A2A discovery in the running
LiteLLM process still failed while direct authenticated discovery succeeded.
Restarting the gateway cleared that state and all three routed turns passed;
workspace startup now refreshes the gateway before integration checks.
The precise internal cache/DNS mechanism was not established.

The regenerated runtime lock also explicitly retains `psycopg-pool` 3.3.3
(required by Mem0's pgvector backend) and greenlet 3.5.6. `pip check` alone
cannot detect an optional backend dependency omitted from installed extras;
the live memory test exposed the missing pool package.

## Initial dependency audit findings — resolved by the Hermes migration

`pip-audit` reported no known vulnerabilities in the installed dependencies of
the retrieval/offline-MCP/execution service test environment. Auditing the full
agent runtime lock initially flagged 15 packages. Compatible updates reduced
that to three packages with upstream Hermes 0.21.3 exact requirements:

| Package retained | Upstream requirement | Example advisory |
| --- | --- | --- |
| certifi 2026.5.20 | `certifi==2026.5.20` | [GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2) |
| Pillow 12.3.0 | `Pillow==12.3.0` | [GHSA-4558-3h8r-v8xp](https://github.com/advisories/GHSA-4558-3h8r-v8xp) |
| PyJWT 2.13.0 | `PyJWT[crypto]==2.13.0` | [GHSA-ffc3-869f-jxw9](https://github.com/advisories/GHSA-ffc3-869f-jxw9) |

The old Hermes requirement pins prevented compatible upgrades of these three
packages. The follow-up replaced that base instead of weakening its old pins.
The official standalone provider is pinned at commit
`43849c6e9d4d773b83a8144b6373aedc67241a5d`, with archive SHA-256
`da2285987bf72e3d89bca3275659c11239a57c0265962f3393027a8c1026b588`.
Its source and license are copied from that verified archive into the image;
there is no runtime catalog lookup. The underlying Mem0 SDK remains at the
current stable 2.2.1, preserving the existing pgvector data format.

The new base also had advisories for oauthlib 4.0.0, opentelemetry-api 1.45.1
and Tornado 6.6. The final lock updates these to 4.0.1, the matching complete
OpenTelemetry 1.45.2 family, and 6.6.1 respectively.

## Hermes migration verification — 2026-11-16

- The final lock audit and an independent export/audit of the installed runtime
  packages both reported **no known vulnerabilities**. The installed export
  includes pip/build tools; only repository-owned workspace code and Hermes'
  synthetic source metadata (not a PyPI release) are excluded from advisory
  lookup. Source/OS vulnerabilities are not ruled out by this Python audit.
- Installed versions confirmed directly: certifi 2026.10.19, Pillow 13.0.0,
  PyJWT 2.15.1, Mem0 SDK 2.2.1; plugin manifest 1.3.0; Python 3.14.9 from the
  Hermes image. The base digest is
  `sha256:276d44685c65c0cb2a25f190b63c65c8906f9dc76486fb1c05ae71b5826bda08`.
- Focused checks passed for PyJWT preserving caller options and rejecting
  expired/wrong-audience tokens, certifi CA loading, Pillow image conversion
  and resizing, and malformed-image rejection. These checks do not exercise
  every advisory's original payload.
- A synthetic fact saved by the old runtime was found by ID and exact text
  after switching to the external provider; review-profile access remained
  isolated. Memory CRUD/fresh-process persistence checks passed on the new image.
- `make runtime-smoke` verifies actual `AIAgent` instances expose all four Mem0
  tools and report `memory_status=ready` for development, review and research.
  Routed execution checks now require ready memory as well as a completed turn.
- Independent read-only candidate review found no confirmed actionable bypass
  or regression. Its Docker access was unavailable; live checks were performed
  by the primary agent.
- `make lint` and `make test` passed: 22 retrieval, 5 offline MCP, 16 execution,
  and 18 script tests. One optional database test remains skipped without
  `MEMORY_TEST_DSN`. Host cargo is absent; the two Rust tests passed separately
  in the pinned Rust 1.100.0 container with `--locked`.
- Private backups of all three profile volumes and the memory database were
  created before agent/session initialization on the new version; no data
  volume or credential identity was deleted or reset.

- Final `make up` passed on the digest-pinned base, followed by separate
  platform, editor and Hermes smoke checks.
  Both CLI and profile paths use the same audited image. Lazy dependency
  installation is explicitly disabled in all generated Hermes templates.
- The original synthetic fact also survived the complete final startup; it was
  then deleted and a fresh process confirmed its absence. No user facts were
  deleted by this verification.
- The one-shot Hermes container emits an upstream managed-artifact warning
  about pnpm, but the local CLI turn completes. Its own optional web frontend
  is not exposed or verified by this platform's CLI smoke test.

This establishes the tested local transport, persistence, authorization and
bounded agent workflows, not arbitrary autonomous task quality, enabled web
browsing, hosted CI, authenticated external GitHub MCP, or all container OS
packages.

## Live database test follow-up — 2026-11-16

The previously skipped `execution/tests/test_memory_storage.py` was executed
against the real provisioned `workspace_memory` database as `memory_development`.
The process first verified its actual login, database and search path, and that
its existing `isolation_probe` fixture was present. The exact pytest test passed:
**1 passed**. The complete execution suite then ran with the same live
`MEMORY_TEST_DSN`: **17 passed, 0 skipped**.

The test confirmed that selecting `memory_review.isolation_probe` raises
`InsufficientPrivilege` and `has_database_privilege(..., 'local_ai', 'CONNECT')`
is false. No grants, passwords, schemas or user memory were changed. The test's
`CREATE TABLE IF NOT EXISTS` targeted the already-provisioned probe table.

Execution used a disposable `workspace-runtime:0.1.0` container on the existing
internal data network, with pytest 9.2.0 installed from downloaded wheels using
`--no-index`. Profile configuration and tests were mounted read-only. The DSN
was assembled inside the process, never printed or placed in command arguments;
no PostgreSQL host port was published and the production image was unchanged.
At this stage, the host-only `make test` still skipped the DB test without a DSN;
the automation follow-up below removes that limitation.

## Automated database test follow-up — 2026-11-16

`make test` and GitHub CI now run the DB isolation test automatically. Its pytest
fixture starts a separate `pgvector/pgvector:0.8.7-pg17-bookworm` container with
tmpfs data and a random loopback-only host port, then runs the production memory
provisioner. Generated credentials stay out of logs, command arguments and
repository files; `MEMORY_TEST_DSN` is exported only within pytest. No platform
`.env`, volumes or running services are needed.

The automatic DB test passed without a supplied DSN, and `make test` reported
**17 passed, 0 skipped** for execution tests. Container removal was verified
after both successful execution and an intentionally failing test. Unavailable
Docker caused a setup error rather than a skipped test. `make lint` and the full
live `make smoke-test` also passed.
