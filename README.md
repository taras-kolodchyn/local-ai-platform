# Local AI Platform

Run Moyai on an Apple Silicon Mac to write and test code in a local sandbox. Qwen
handles inference through LiteLLM, PostgreSQL and pgvector supply cited repository
context, and Moyai returns files for download. The default profile runs locally
after the initial downloads using local credentials.

## Quick start

Use an Apple Silicon Mac with Docker Desktop, Docker Compose and Docker Model
Runner. The default Qwen3-Coder artifact is about 16.5 GB. Allow at least 30 GiB
of free disk space for a fresh setup. The host check requires 32 GiB of unified
memory. For the default 65,536-token context, use 48 GiB or more. With 32–47 GiB,
run `make bootstrap` after cloning and set `QWEN_CONTEXT_SIZE=32768` in the
generated `.env` before starting. The project recorded Docker Desktop 4.99.0 on
2026-11-16 and Model Runner 1.2.9 on 2026-11-17. [Docker Model Runner](https://docs.docker.com/ai/model-runner/) ·
[Desktop environment](docs/dependency-update-2026-11-16.md) ·
[Model Runner environment](docs/results/repository-workflow-2026-11-17.md#runtime-identity)

Install Git, Make, curl, jq, OpenSSL, Python and Cargo. The test suite supports
Python 3.11–3.14. Check that `cargo --version` works. The first setup needs
internet access for images, model artifacts and build dependencies.
[Rust installation](https://rust-lang.org/tools/install/) · [Offline preparation](docs/offline.md)

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

Use `make status` to see the services and resident models. `make down` stops
Moyai, then the core services and any connected profile, and unloads the
configured coding and embedding models. It preserves conversations, volumes and
downloaded model artifacts. `make restart` performs the full stop/start cycle.
[Platform operations](docs/platform-operations.md) · [Docker model unload](https://docs.docker.com/reference/cli/docker/model/unload/)

## How requests run

| Task | Execution path |
| --- | --- |
| Chat and code generation | Moyai or an OpenAI-compatible client → LiteLLM → Docker Model Runner on the Mac. The llama.cpp inference engine uses Metal in Docker Desktop's macOS host sandbox. |
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

`make up` seeds **Skills** in the LiteLLM dashboard with 25 text-only engineering
workflows. Each turn pins the enabled catalog. Qwen selects relevant guides from
their summaries and loads them through native `load_capability` calls. A simple
question can select zero. The guides work offline. The active chat or repository
phase still controls tool permissions. The integration was checked on 2026-11-18 with LiteLLM 1.109.1
and Pydantic AI 2.55.0. [Local guides](integrations/moyai/skills) ·
[Upstream catalog and license](https://github.com/addyosmani/agent-skills/tree/39b723ceebf81474d0557c1fecd01eddab585563) ·
[LiteLLM marketplace](https://docs.litellm.ai/docs/tutorials/claude_code_plugin_marketplace)

Edit a guide's **Description** to change its one-line summary and Markdown
instructions, separated by a blank line. The `moyai-text-v1` keyword identifies
this format. Use **Enabled** to control availability. Edits take effect on the
next turn and survive another `make up`. The marketplace entry
`addyosmani-test-driven-development` uses the native ID `test-driven-development`.

### Repository search and verified changes

For a searchable local repository, use `make index REPO=/absolute/path/to/repository`.
Indexing reuses compatible embeddings for unchanged chunks. Hybrid search fuses
vector and full-text ranks and applies repository and path filters. The vector
store's `score_threshold` uses normalized hybrid relevance. Raw cosine similarity
is available separately. [Retrieval and scoring](docs/platform-operations.md) ·
[PostgreSQL full-text search](https://www.postgresql.org/docs/17/textsearch-controls.html) ·
[pgvector hybrid search](https://github.com/pgvector/pgvector#hybrid-search)

To try a complete repair with the running stack, use `make repository-smoke`.
It stages the bundled Rust example from the pinned commit before its retry
policy was repaired. Moyai retrieves cited context, records a plan, edits a copy
and runs protected checks. The observer downloads `changes.patch`,
`test-report.json` and `result.json` and checks the patch against the verified
tree. A full clone includes the demonstration commit. For controlled execution
on an operator-selected snapshot, `make repository-add REPO=/absolute/path`
registers a source tree matching the supported `rust-retry` profile.
[Repository workflow and shallow-clone instructions](docs/repository-workflow.md) ·
[Rust demo](examples/rust-service/README.md) ·
[Recorded repair and download](docs/results/repository-reliability-2026-11-17.md)

### Prompt compression and caching

LiteLLM sends eligible history (such as older tool output) to the private
Headroom sidecar before inference. It preserves system instructions, the latest
user message and the latest assistant tool exchange. If Headroom is unavailable,
LiteLLM forwards the original context. Use the header `x-headroom-bypass: true`
for a one-request comparison. [LiteLLM integration](https://docs.litellm.ai/docs/proxy/headroom) ·
[Headroom compression API](https://docs.headroomlabs.ai/docs/proxy#post-v1compress)

On 2026-11-20, LiteLLM 1.110.2 and Headroom 0.44.0 reduced a synthetic 240-line
log from 6,558 to 280 reported input tokens on both Chat Completions and Responses.
Both routes returned the exact error code. In a separate 2026-11-19 comparison
with LiteLLM 1.109.1, an unstructured text sample showed no reduction. In two
independently accepted Rust repairs that day, the baseline used 239,112 input tokens over
20 calls in 213.8 seconds. The compressed run used 267,634 over 22 calls in
241.2 seconds and Headroom reported removing 611 tokens. The runs took different
tool steps. `python3 scripts/compression-smoke.py` repeats the synthetic log
comparison on the running stack.

Redis stores exact responses only for eligible chat requests that explicitly
opt in with `"cache": {"use-cache": true}`. The default TTL is 300 seconds.
Semantic response caching is disabled. The inference engine owns its KV cache.
PostgreSQL/pgvector stores source chunks and reusable embeddings for retrieval.
[Gateway cache configuration](litellm/config.yaml) · [Cache terminology](docs/architecture.md#cache-taxonomy)

## Optional GitHub workflows

| To do this | Enable this workflow |
| --- | --- |
| Read GitHub from an MCP client | Configure `.env.connected` and run `make connected-up`. The official GitHub MCP server exposes nine allowlisted read-only tools through LiteLLM. `make connected-down` stops it. [Connected mode](docs/connected.md) |
| Publish a verified Rust repair or coverage change | Start a foreground publisher on the Mac, then submit the issue URL in a private Moyai chat. The host verifies the delivered patch and opens a draft PR. The GitHub App can also merge after verified CI if explicitly granted `AUTO_MERGE=1` for one issue. GitHub credentials stay on the host. [Issue publisher and supported profiles](docs/moyai-github-issues.md) |
| Request documentation for the bundled Rust example | When the Moyai Developer and Verifier schedules are configured, the Developer picks up an owner-filed issue labeled `moyai:ready` and opens a linked PR. The Verifier reviews the proposed commit. GitHub Actions checks the verdict, the single-file scope and PR CI, then merges and posts the `main` CI result on the issue. [Issue form](.github/ISSUE_TEMPLATE/moyai-docs.yml) · [Progress workflow](.github/workflows/moyai-progress.yml) · [Merge workflow](.github/workflows/moyai-merge.yml) |

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
a working Docker engine, Python 3.11–3.14 and Cargo. It runs Python and contract
tests, real database checks using a disposable PostgreSQL 17 / pgvector 0.8.7
container on loopback, and the Rust example with `cargo test --locked`. The
database fixture uses tmpfs and cleans up after the run. A provisioned disposable
database can be supplied through `MEMORY_TEST_DSN`. Models and a running platform
stack are unnecessary for this suite.

`make smoke-test` requires the running stack and checks the local model routes,
tool calls, compression, cache, retrieval, MCP, dashboard logins and metrics. For
end-to-end acceptance, `make verify` runs the suite, starts the platform, exercises
real Moyai sandbox and repository tasks with service restarts, and attempts full
shutdown even on failure. It checks that the stack's containers and resident
models are gone. Its private logs and result receipt are in `.local/verification/`.
[Verification phases](scripts/verify.py) · [Recorded acceptance and limits](docs/results/repository-workflow-2026-11-17.md)

To exercise skill selection separately, run `python3 scripts/moyai-skills-smoke.py`
for the Rust planning case, or add `--case latency` or `--case arithmetic`. Each
case creates a private chat and checks the chosen workflows, native loading and
inline answer. `make help` lists all supported commands.

## Shared dashboard login (optional)

Bootstrap generates separate credentials for the LiteLLM and Grafana dashboards.
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

The smoke check opens fresh sessions using `UI_USERNAME` and `UI_PASSWORD` for
LiteLLM (the default password is `LITELLM_MASTER_KEY`), and `admin` and
`GRAFANA_ADMIN_PASSWORD` for Grafana. User ID 1 is this stack's initial Grafana
admin. Dashboard logins were verified on 2026-11-20 with LiteLLM 1.110.2 and
Grafana 13.3.1. [LiteLLM UI credentials](https://docs.litellm.ai/docs/proxy/ui) ·
[Grafana password configuration](https://grafana.com/docs/grafana/latest/setup-grafana/configure-grafana/#admin_password) ·
[Grafana admin CLI](https://grafana.com/docs/grafana/latest/administration/cli/)

## Documentation

- [Architecture](docs/architecture.md), [macOS inference decision](docs/adr/0001-inference-runtime.md) and [platform operations](docs/platform-operations.md)
- [Local Moyai](docs/moyai-local.md), [repository tasks](docs/repository-workflow.md) and [UI verification](docs/moyai-ui-verification.md)
- [Troubleshooting](docs/troubleshooting.md), [benchmark method](docs/benchmarking.md), [historical capability matrix](docs/capability-matrix.md) and [July agent validation](docs/results/agent-validation-2026-07-19.md)
- [Implementation plan](docs/implementation-plan.md), [November 16 dependency report](docs/dependency-update-2026-11-16.md) and [retired workspace profiles](docs/workspace-memory.md)
- [Security design](docs/security.md), [risk register](docs/risks.md) and [vulnerability reporting](SECURITY.md)

![Provisioned Local AI Platform dashboard](output/playwright/grafana-local-ai-platform.png)

## License

[Apache-2.0](LICENSE). The bundled [Moyai integration](integrations/moyai/LICENSE.upstream)
and [engineering guides](integrations/moyai/skills/LICENSE) include their upstream licenses.
