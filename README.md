# Local AI Platform

Run a local AI coding platform on an Apple Silicon Mac or a Linux x86_64/Arm64
host. Moyai edits and tests code in an isolated workspace, Qwen serves requests
through LiteLLM, and pgvector searches the repositories you choose to index.
After the initial downloads, the default profile runs locally with local
credentials.

**Ubuntu support is in alpha.** We do not guarantee stability on Ubuntu during this testing phase.

## Quick start

Use an Apple Silicon Mac with Docker Desktop, or a Linux host with Docker Engine,
together with Docker Compose and Docker Model Runner. On Docker Engine, install
the runner with `sudo apt-get install docker-model-plugin`; its TCP endpoint on
port 12434 is enabled by default. The default Qwen3-Coder artifact is about
16.5 GB. Allow at least 30 GiB of free disk space for a fresh setup. The host
check requires 32 GiB of memory (unified memory on a Mac, system RAM on Linux).
For the default 65,536-token context, use 48 GiB or more. With 32–47 GiB, run
`make bootstrap` after cloning and set `QWEN_CONTEXT_SIZE=32768` in the generated
`.env` before starting. See the host and service versions in the
[compatibility matrix](docs/capability-matrix.md).
[Docker Model Runner](https://docs.docker.com/ai/model-runner/) ·
[DMR on Docker Engine](https://docs.docker.com/ai/model-runner/get-started/)

Install Git, Make, curl, jq, OpenSSL and Python. The test suite supports
Python 3.11–3.14. The first setup needs
internet access for images, model artifacts and build dependencies.
[Offline preparation](docs/offline.md)

```sh
git clone https://github.com/taras-kolodchyn/local-ai-platform.git
cd local-ai-platform
make up
```

`make up` creates local credentials, renders the gateway configuration, prepares
the models, checks the host, starts the core services and Moyai, and runs live
smoke checks. Open [Moyai](http://127.0.0.1:8787) and sign in as `admin` using the
generated password in `.local/moyai/admin-password`. For a first task, ask it to
create a Python function and a test, run pytest and save both files. Open **Files**
in the conversation to download the artifacts.

Use `make up TIMINGS=1` to see which containers Compose is waiting for and
how long the parallel Qwen and retrieval/MCP checks take.
[Startup timings](docs/benchmarking.md#startup-timings)

Use `make status` to see the services and resident models. `make down` stops
Moyai, then the core services and any connected profile, and unloads the
configured coding and embedding models. It preserves conversations, volumes and
downloaded model artifacts. `make restart` performs the full stop/start cycle.
[Platform operations](docs/platform-operations.md) · [Docker model unload](https://docs.docker.com/reference/cli/docker/model/unload/)

## How requests run

| Task | Execution path |
| --- | --- |
| Chat and code generation | Moyai or an OpenAI-compatible client → LiteLLM → Docker Model Runner on the host. The llama.cpp inference engine uses Metal in Docker Desktop's macOS host sandbox, or runs in a container on Linux using the CPU (or an NVIDIA/AMD GPU when a supported driver is present). |
| File edits and commands | Moyai connects over SSH to a separate Linux sandbox. Temporal persists workflow state and Moyai delivers saved files through the local UI. |
| Repository search | An internal bridge uses a dedicated search credential for the selected snapshot and indexed generation. Retrieval combines pgvector cosine search with PostgreSQL full-text search over source, symbols and paths. |
| Observability | Prometheus collects metrics, Alloy sends service logs to Loki, and Grafana displays them locally. Request and response body logging is off by default. |

Moyai uses an inference credential restricted to `local-qwen` and runs commands in
a sandbox with a private workspace and no mounted Docker socket, host home or
writable source checkout. This setup is designed for one trusted local operator.
Conversations have separate directories and share one sandbox OS user. Treat
repository content, tool output and retrieved passages as untrusted data.
[Inference engines and Metal](https://docs.docker.com/ai/model-runner/inference-engines/) ·
[Local Moyai architecture](docs/moyai-local.md) · [Security design](docs/security.md)

### Models and access

Point an OpenAI-compatible client at `http://127.0.0.1:4000/v1` and use the scoped
virtual key provisioned by `make up`. For a shell client, `source .local/client.env`
loads the key as `LITELLM_API_KEY`. Client requests use these stable aliases.

| LiteLLM alias | Default model | Use |
| --- | --- | --- |
| `local-qwen` | Qwen3-Coder 30B-A3B, UD-Q4_K_XL | Chat Completions and streamed Responses with tool calls |
| `local-embeddings` | Qwen3-Embedding 0.6B, F16 | 1,024-dimensional repository embeddings |
| `local-reranker` | MS MARCO MiniLM-L6-v2, ARM64 INT8 | Local CPU ranking when repository search needs a second pass |

The stack pins LiteLLM **1.110.2**. On 2026-11-20 the dashboard API returned all
three aliases and their configured model metadata. Bootstrap renders the
[LiteLLM template](litellm/config.yaml) into `.local/litellm/config.yaml` using
the model references and context from `.env`. `make status FORMAT=json` reports
the resolved settings, artifact identities and residency without starting the
services. Requested context, artifact limits and process RSS are separate
measurements. [Default model settings](.env.example) ·
[LiteLLM 1.110.2](https://github.com/BerriAI/litellm/releases/tag/v1.110.2) ·
[Model management](https://docs.litellm.ai/docs/proxy/ui_store_model_db_setting) ·
[Pinned reranker assets](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2/tree/df500e56a59fc34f37322735a40eff666200312e/onnx)

### Local services

All published ports bind to loopback.

| Service | Address |
| --- | --- |
| Moyai web app | [Open a chat](http://127.0.0.1:8787) |
| LiteLLM dashboard | [Manage the gateway](http://127.0.0.1:4000/ui) |
| LiteLLM API | `http://127.0.0.1:4000/v1` |
| Retrieval API | `http://127.0.0.1:8000` |
| Offline MCP tools | `http://127.0.0.1:8001/mcp/` |
| Grafana | [View dashboards](http://127.0.0.1:3000) |
| Prometheus | [Query metrics](http://127.0.0.1:9090) |

### Analysis and skills in Moyai

Choose **local-qwen-analysis** in Moyai's model picker to read existing files and
discuss plans, code or architecture decisions in chat. This Moyai profile uses
the `local-qwen` gateway alias and limits workspace tools to reading and listing.
Starting a web message with `/analysis` or ending it with “без змін у файлах”
also selects the profile for the chat. Choose **local-qwen** to resume editing.
[Pydantic AI read-only workspace](https://pydantic.dev/docs/ai/core-concepts/workspace/#read-only-access)

`make up` seeds **Skills** in the LiteLLM dashboard with 25 offline engineering
guides. Qwen selects relevant guides for each turn and loads them within the
chat's existing tool permissions. Edit or disable a guide in the dashboard to
change what Moyai can select on the next turn. [Managing skills](docs/moyai-local.md#skills-and-context) ·
[Upstream catalog and license](https://github.com/addyosmani/agent-skills/tree/39b723ceebf81474d0557c1fecd01eddab585563)

### Repository search and verified changes

To use your own project, point the indexer at its separate checkout. The ingestion
container mounts that path read-only. Search combines embedding matches with text
matches on paths, symbols and content. [Retrieval and scoring](docs/platform-operations.md#hybrid-retrieval)

```sh
make index REPO=/absolute/path/to/your-project
```

To keep a search index current after merges, run these commands from the permanent
platform checkout on the Mac after `make up`.

```sh
make rag-connect REPO=owner/project
make rag-status
```

Connect once per repository. The default branch is `main`. Use `BRANCH=release`
to follow another branch. Private GitHub repositories need a Git HTTPS credential
helper that works without a terminal. An absolute path to a local Git repository
is also supported. Only commits on the selected branch enter this index.

A per-user macOS job checks the branch every minute while logged in and catches
up after sleep. A new tip triggers an export of the committed tree. Ingestion
reuses unchanged chunks and removes chunks for deleted files. The store ID stays
stable across updates. `make rag-status` shows the schedule, observed and indexed
commit, last check, publication time, store ID and any error. A failed update
is retried on a later poll. After restoring or resetting the database, rerun
the connect command to reconcile the index even if the branch tip is unchanged.

To stop polling and remove the branch's indexed content and LiteLLM store, run
`make rag-disconnect REPO=owner/project`. Supply the same `BRANCH` for a
non-default branch. The last disconnect unloads the polling schedule. If cleanup
is interrupted, the connection stays paused. Repeat the same command to finish.

Search through `POST /v1/vector_stores/{store_id}/search` using a LiteLLM key
with read access to that store. By default a connection grants no client access.

For a private Moyai conversation, pass its session UUID with `SESSIONS`.
Use a comma-separated list to grant the same branch to several sessions.
Add `POLL=0` for a one-time index without installing a schedule.

```sh
make rag-connect REPO=owner/project BRANCH=main SESSIONS="$DEVELOPER_SESSION" POLL=0
make rag-connect REPO=owner/project BRANCH=feature/topic SESSIONS="$VERIFIER_SESSION" POLL=0
```

Start a dedicated lookup with `Search source for https://github.com/owner/project/issues/42 followed by your question`.
The same command accepts a pull request URL. It runs one repository search and
answers from its result. A normal private request can also include one work-item URL. Ask for a
passage, its path and its Git SHA. For an issue, search uses the connected
default branch. For a PR it requires a connected branch at the same commit
as the PR head observed by Git during synchronization. A fork's PR therefore
requires a matching commit on a connected branch of the target repository.
Citations identify the indexed revision. Repeat a one-time connection to
refresh its Git refs. Disconnecting a branch revokes new searches from its
granted sessions. Previously delivered citations remain in chat history.
The default startup continues to check only the two platform probe documents.

For projects matching the protected `rust-retry` profile,
`make repository-add REPO=/absolute/path/to/crate` also enables verified edits in a private copy.
Moyai delivers a patch, test report and result receipt through **Files**.
[Supported inputs and verification](docs/repository-workflow.md)

### Prompt compression and caching

LiteLLM sends eligible history (such as older tool output) to the private
Headroom sidecar before inference. It preserves system instructions, the latest
user message and the latest assistant tool exchange. If Headroom is unavailable,
LiteLLM forwards the original context. Use the header `x-headroom-bypass: true`
for a one-request comparison. [LiteLLM integration](https://docs.litellm.ai/docs/proxy/headroom) ·
[Headroom compression API](https://docs.headroomlabs.ai/docs/proxy#post-v1compress)

Run `python3 scripts/compression-smoke.py` to compare input token counts with
compression enabled and bypassed. The check also verifies that both routes
can recover the error code from the same synthetic log.

Redis stores exact responses only for eligible chat requests that explicitly
opt in with `"cache": {"use-cache": true}`. The default TTL is 300 seconds.
Semantic response caching is disabled. The inference engine owns its KV cache.
PostgreSQL/pgvector stores source chunks and reusable embeddings for retrieval.
[Gateway cache configuration](litellm/config.yaml) · [Cache terminology](docs/architecture.md#cache-taxonomy)

## Optional GitHub workflows

| To do this | Enable this workflow |
| --- | --- |
| Read GitHub from an MCP client | Configure `.env.connected` and run `make connected-up`. The official GitHub MCP server exposes nine allowlisted read-only tools through LiteLLM. `make connected-down` stops it. [Connected mode](docs/connected.md) |
| Publish a verified change from a supported external project | Set the target repository in the local configuration, start the host publisher, and submit its issue URL in a private Moyai chat. The host verifies the patch before opening a draft PR. [Prerequisites and publication](docs/moyai-github-issues.md) |

Ordinary `make up` starts Moyai with local tools and no external publisher. The
offline MCP servers allow scoped retrieval and read-only filesystem, Git and
PostgreSQL operations. Runtime operation is local after preparation. Network-wide egress enforcement requires a separate firewall policy. [Offline operation](docs/offline.md)

## Verification

```sh
make lint
make test
make smoke-test
```

`make lint` checks shell, Python, Compose and configuration. `make test` requires
a working Docker engine and Python 3.11–3.14. It runs platform unit and contract
tests and checks database isolation using a disposable PostgreSQL 17 / pgvector
0.8.7 container on loopback. The database fixture uses tmpfs and cleans up after
the run. A provisioned disposable
database can be supplied through `MEMORY_TEST_DSN`. Models and a running platform
stack are unnecessary for this suite.

`make smoke-test` requires the running stack and checks the local model routes,
tool calls, compression, cache, retrieval, MCP, dashboard logins and metrics. For
end-to-end acceptance, `make verify` runs the suite, starts the platform, exercises
a real Moyai sandbox task with a service restart, and attempts full
shutdown even on failure. It checks that the stack's containers and resident
models are gone. Its private logs and result receipt are in `.local/verification/`.
Supply `REPO=/absolute/path/to/crate` to include the optional, profile-specific
repository task. [Verification phases](scripts/verify.py)

To exercise skill selection separately, run `python3 scripts/moyai-skills-smoke.py`
for the Rust planning case, or add `--case latency` or `--case arithmetic`. Each
case creates a private chat and checks the chosen workflows, native loading and
inline answer. `make help` lists all supported commands.

## Shared dashboard login (optional)

LiteLLM uses the credentials in `.env`. A blank `UI_PASSWORD` uses the master
key. Grafana has its own generated admin password.
For a shared login, run `make bootstrap` and edit the following entries in the
generated, Git-ignored `.env`. Use the same password in both password fields.
Single quotes keep shell characters such as `$` literal.

```dotenv
UI_USERNAME=admin
UI_PASSWORD='YOUR_SHARED_LOCAL_PASSWORD'
GRAFANA_ADMIN_PASSWORD='YOUR_SHARED_LOCAL_PASSWORD'
```

Apply the settings with `make up`. When reusing a Grafana volume, Grafana also
needs its stored admin password updated. Once the container is running with the
new environment, run this command to pass its configured password through stdin,
then rerun `make smoke-test` if startup reported a failed login.

```sh
docker compose exec -T grafana sh -c '
  printf "%s\n" "$GF_SECURITY_ADMIN_PASSWORD" |
    grafana cli --homepath /usr/share/grafana --config /etc/grafana/grafana.ini \
      admin reset-admin-password --password-from-stdin --user-id 1
'
```

In local-password mode, the smoke check opens fresh sessions using `UI_USERNAME` and `UI_PASSWORD` for
LiteLLM (the default password is `LITELLM_MASTER_KEY`), and `admin` and
`GRAFANA_ADMIN_PASSWORD` for Grafana. User ID 1 is this stack's initial Grafana
admin. Dashboard logins were verified on 2026-11-20 with LiteLLM 1.110.2 and
Grafana 13.3.1. [LiteLLM UI credentials](https://docs.litellm.ai/docs/proxy/ui) ·
[Grafana password configuration](https://grafana.com/docs/grafana/latest/setup-grafana/configure-grafana/#admin_password) ·
[Grafana admin CLI](https://grafana.com/docs/grafana/latest/administration/cli/)

## Documentation

- **Set up and operate** — [offline preparation](docs/offline.md), [platform operations](docs/platform-operations.md), [troubleshooting](docs/troubleshooting.md) and [benchmarking](docs/benchmarking.md).
- **Use Moyai** — [chat, files and skills](docs/moyai-local.md), [verified repository tasks](docs/repository-workflow.md), [read-only GitHub access](docs/connected.md) and [issue-to-PR publishing](docs/moyai-github-issues.md).
- **Understand the stack** — [architecture](docs/architecture.md), [inference runtime decision](docs/adr/0001-inference-runtime.md) and [compatibility matrix](docs/capability-matrix.md).
- **Contribute and report issues** — [contributing](CONTRIBUTING.md), [security boundaries](docs/security.md) and [private vulnerability reporting](SECURITY.md).

## License

[Apache-2.0](LICENSE). The bundled [Moyai integration](integrations/moyai/LICENSE.upstream)
and [engineering guides](integrations/moyai/skills/LICENSE) include their upstream licenses.
