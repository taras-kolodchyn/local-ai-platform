# Local AI Platform

An open-source, reproducible local AI platform for coding agents on Apple Silicon, powered by Qwen, LiteLLM, Docker Model Runner, Codex, Hermes, MCP, Redis, PostgreSQL/pgvector, and Grafana.

> Status: validated Apple Silicon MVP. Core gateway, model, cache, retrieval,
> offline MCP, and observability paths passed live tests on a 48 GiB arm64 Mac.
> The autonomous local-model coding exercise did not pass its acceptance
> criteria; that limitation is documented instead of being hidden.

Current dependency pins and upgrade verification: [2026-11-16 update](docs/dependency-update-2026-11-16.md).

## Why this exists

A coding agent is more than a local chat UI. It needs a model gateway, tool permissions, retrieval, caching, auditability, and a safe execution boundary. This repository puts those pieces behind one OpenAI-compatible endpoint:

```text
Codex / Hermes / other clients -> LiteLLM -> local Qwen on Apple Silicon
                                      |
                                      +-> Redis response cache
                                      +-> MCP Gateway
                                      +-> PostgreSQL + pgvector retrieval
                                      +-> Prometheus / Loki / Grafana
```

## Quick start target

Requirements: an Apple Silicon Mac and Docker Desktop 4.40 or newer with at least 32 GiB unified memory. The default 30B coding model is approximately 16.5 GB before runtime and KV-cache overhead; 48 GiB is recommended for a useful coding context.

```sh
git clone https://github.com/taras-kolodchyn/local-ai-platform.git
cd local-ai-platform
make up
```

`make up` diagnoses the host, creates a local `.env`, enables Docker Model Runner, pulls pinned Qwen model artifacts, starts the core services, persistent execution profiles and local Moyai agent, waits for health checks, verifies the gateway and cache, and writes client configuration examples under `.local/`.

`make down` stops Moyai, then all platform containers (including previously
enabled profiles), then unloads the configured coding and embedding models from
Model Runner memory. Volumes, conversations and downloaded model artifacts stay
on disk. `make restart` performs this same stop/start cycle. `make status` shows
both service groups and distinguishes resident models from downloaded artifacts.

Moyai keeps its isolated Compose definition and existing data volumes, while the
public commands manage it as part of the complete stack. The shutdown order
removes its bridge before the core gateway network, then unloads inference after
all platform clients have stopped. A failed cleanup phase makes `make down` fail
after attempting the remaining phases, so it cannot silently report success.
Managed commands pin the canonical Compose project names and files, ignore
ambient Compose overrides, and load model settings from `.env` for both startup
and shutdown. Connected profiles require their explicit opt-in command.

Model unloading uses the official [`docker model unload`](https://docs.docker.com/reference/cli/docker/model/unload/)
command for `QWEN_MODEL` and `EMBEDDING_MODEL` from `.env`; unrelated models and
Docker projects are outside this shutdown. Without `.env`, shutdown uses the
repository defaults and reports that fallback. Docker Desktop remains running;
macOS may retain reclaimable file cache after inference allocations are released.
Verified 2026-11-16 with Docker CLI 29.9.3 and Docker Model Runner 1.2.9.

Use the native Codex sidebar in VS Code while keeping the generated provider,
credentials, sessions, and MCP configuration isolated from your normal
`~/.codex`:

```sh
make vscode-install
make vscode-check
make vscode-smoke
make vscode
```

The install target is a one-time connected step. Daily use is `make up` followed
by `make vscode`. Open **Codex: Open Codex Sidebar** from the Command Palette and
keep execution on **Local**. See the [VS Code workflow](docs/vscode.md) for the
security boundary and troubleshooting.

The terminal client remains available with the same generated configuration:

```sh
source .local/client.env
CODEX_HOME="$PWD/.local/codex" codex
```

The generated Codex configuration uses LiteLLM's Responses API bridge and reaches only the allowlisted, read-only MCP tools through the LiteLLM MCP Gateway. The repository `AGENTS.md` keeps retrieved text in the untrusted-data boundary.

Useful commands:

```sh
make help
make doctor
make status
make logs
make vscode
make vscode-check
make vscode-smoke
make smoke-test
make index REPO=/absolute/path/to/repository
make hermes-smoke
make connected-up
make down
```

## Tests

`make test` runs unit and contract tests plus real PostgreSQL memory isolation.
The DB test starts a disposable `pgvector/pgvector:0.8.7-pg17-bookworm` container,
binds a random port on `127.0.0.1`, and runs the production memory provisioner.
Its data lives in tmpfs; the container is removed after success or failure.
This requires a running Docker engine, but no `.env`, models, or running platform
stack. Database startup or provisioning failures fail the test instead of
skipping it. GitHub CI runs the same fixture automatically.

For an explicitly provisioned disposable database, `MEMORY_TEST_DSN` can still
be supplied to use that database instead. The default generates temporary
credentials and exports the DSN only inside pytest.

## Endpoints

All host-facing services bind to loopback by default.

| Service | URL |
| --- | --- |
| LiteLLM API | `http://127.0.0.1:4000/v1` |
| LiteLLM UI | `http://127.0.0.1:4000/ui` |
| Retrieval API | `http://127.0.0.1:8000` |
| Offline MCP tools | `http://127.0.0.1:8001/mcp/` |
| Grafana | `http://127.0.0.1:3000` |
| Prometheus | `http://127.0.0.1:9090` |
| Moyai agent | `http://127.0.0.1:8787` |

Client-facing aliases are `local-qwen` and `local-embeddings`. The Docker Model Runner artifact names are internal implementation details.

## Shared dashboard login (optional)

To use `admin` and one chosen password for both local dashboards, first run
`make bootstrap` from the repository root. Edit these entries in the generated,
Git-ignored `.env`, replacing both placeholders with the same password:

```dotenv
UI_USERNAME=admin
UI_PASSWORD='YOUR_SHARED_LOCAL_PASSWORD'
GRAFANA_ADMIN_PASSWORD='YOUR_SHARED_LOCAL_PASSWORD'
```

Keep the single quotes around the password so characters such as `$` remain
literal. Keep `.env` private; only these dashboard entries need changing.
This is an opt-in setup: bootstrap normally generates separate credentials.

Apply the settings using the supported entry point:

```sh
make up
```

For an existing Grafana data volume, also update its stored admin password;
changing the environment variable alone does not replace it. This command reads
the configured password inside the container and passes it through stdin,
without putting the value in shell history or command arguments:

```sh
docker compose exec -T grafana sh -c '
  printf "%s\n" "$GF_SECURITY_ADMIN_PASSWORD" |
    grafana cli --homepath /usr/share/grafana --config /etc/grafana/grafana.ini \
      admin reset-admin-password --password-from-stdin --user-id 1
'
```

User ID `1` is the default Grafana admin created by this stack. Sign in with
`admin` and your chosen password at [LiteLLM](http://127.0.0.1:4000/ui) and
[Grafana](http://127.0.0.1:3000). Sign out of an existing browser session first
to verify the new login.

`make smoke-test` (also run by `make up`) checks fresh logins using `.env`:
LiteLLM uses `UI_USERNAME` (default `admin`) and `UI_PASSWORD` (falling back to
`LITELLM_MASTER_KEY` when empty); Grafana uses `admin` and
`GRAFANA_ADMIN_PASSWORD`. The check requires a new LiteLLM UI session and
verifies the Grafana session's admin identity. A stale Grafana volume password
therefore fails the smoke test. Passwords and session tokens are kept out of
test output and artifacts.

Verified on 2026-11-16 with LiteLLM 1.109.1 and Grafana 13.3.1. Upstream references:
[LiteLLM UI credentials](https://docs.litellm.ai/docs/proxy/ui) and
[Grafana password configuration](https://grafana.com/docs/grafana/latest/setup-grafana/configure-grafana/#admin_password)
and [admin CLI](https://grafana.com/docs/grafana/latest/administration/cli/).

## macOS inference boundary

Docker Model Runner uses llama.cpp with automatic Metal acceleration on Apple Silicon. On macOS, its inference engine does not run in a normal Linux container; Docker Desktop runs it in a host sandbox and exposes an API to containers. See [ADR 0001](docs/adr/0001-inference-runtime.md) for the decision and fallback design.

## Documentation

- [Architecture](docs/architecture.md)
- [Verified capability matrix](docs/capability-matrix.md)
- [Implementation plan](docs/implementation-plan.md)
- [Inference runtime ADR](docs/adr/0001-inference-runtime.md)
- [Threat model and security design](docs/security.md)
- [Risk register](docs/risks.md)
- [Offline operation](docs/offline.md)
- [Native VS Code workflow](docs/vscode.md)
- [Local Moyai coding MVP](docs/moyai-local.md)
- [Opt-in connected mode](docs/connected.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Benchmark method](docs/benchmarking.md)
- [Measured results and agent validation](docs/results/agent-validation-2026-07-19.md)
- [Ukrainian DOU article draft](docs/dou-article.md)

![Provisioned Local AI Platform dashboard](output/playwright/grafana-local-ai-platform.png)

## Security posture

The default profile is offline and localhost-only. MCP tools are allowlisted, external credentials are absent, request bodies are not shipped to a remote telemetry service, and the Docker socket is not mounted. Local does not mean automatically safe: code, documentation, models, containers, MCP servers, and generated shell commands remain part of the attack surface.

See [SECURITY.md](SECURITY.md) for reporting vulnerabilities.

## License

Apache-2.0. See [LICENSE](LICENSE).

See [local knowledge and persistent profiles](docs/workspace-memory.md) for vector stores, skills, execution, and semantic memory.
