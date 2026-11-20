# Local Moyai coding MVP

This integration runs the real upstream Moyai web app, API, agent,
dispatcher and Temporal workflow with the platform's `local-qwen` alias. Agent
commands execute over SSH in a separate local Linux container. Inference remains
behind LiteLLM and Docker Model Runner's existing macOS Metal boundary.

For cited context, controlled repository edits and independently verified patch
delivery, see [repository tasks](repository-workflow.md). Unattached conversations
retain the file/shell behavior documented here.

## Default repository search

Moyai asks the pinned vector store for automatic ranking through LiteLLM. The backend
searches up to 48 hybrid candidates in the authorized snapshot and generation.
An exact-path filter applies before candidate selection. The default output is six
chunks. An explicit tool limit still controls the result count.

The fast path keeps hybrid order when the leading implementation file also has the
best cosine similarity and its lead exceeds the mean adjacent gap among the top
six distinct implementation files. A single implementation file needs no rerank.
Otherwise, MiniLM scores overlapping 1,600-character windows with a 1,300-character
stride through the `local-reranker` alias. Each chunk takes its best window rank.
Selection prefers up to two chunks per file. In hybrid order a third chunk from
the leading implementation file qualifies if that file occurs three times among
the first six raw candidates. Remaining slots can be filled from other candidates.

The source budget is 24,000 characters. The first chunk is kept whole when it fits.
The other chunks share the remaining budget, with unused space from shorter chunks
redistributed. Truncated excerpts keep complete lines from the beginning and end.
A standalone ellipsis marks a gap. Citations identify the original chunk's line
range. Indexing and existing conversations need no migration.

`make up` verifies the pinned ARM64 INT8 model and tokenizer under `.local/models`
and downloads missing assets during setup. Inference runs locally on CPU behind
LiteLLM with no published reranker port. Verified on 2026-11-19 with LiteLLM
1.109.1 and model revision `df500e56a59fc34f37322735a40eff666200312e`.
[LiteLLM rerank API](https://docs.litellm.ai/docs/rerank) ·
[MiniLM ONNX artifacts](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2/tree/df500e56a59fc34f37322735a40eff666200312e/onnx)

## Conversation history and citations

Moyai gives its local model profile a working window of one quarter of
`QWEN_CONTEXT_SIZE` to leave capacity for concurrent inference. With a
65,536-token configuration, Moyai's native summarizer is eligible to fold older
history at an estimated 12,288 tokens. Headroom still processes eligible content
at the gateway. Its token savings cover only the portions submitted for compression.

Opening a standalone `rust-retry` task retrieves the profile's policy document
from the pinned index. Moyai records the returned source range with the task.
The final chat message cites the recorded policy and implementation ranges when
available. The downloadable `result.json` retains the full citation list.

Verified with Pydantic AI 2.55.0, Pydantic AI Harness 0.55.0 and LiteLLM 1.109.1
on 2026-11-19. [Docker Model Runner configuration](https://docs.docker.com/ai/model-runner/configuration/)
and [Headroom compression API](https://docs.headroomlabs.ai/docs/proxy#post-v1compress).

## Start and sign in

```sh
make up
```

The complete platform startup prepares private local credentials, then builds and
starts Moyai after the gateway is ready. It is the single default active agent;
the former development/review/research A2A profiles are retired. Its
`local-ai-moyai` Compose project remains isolated for networking and storage, but
the public `make up` / `make down` commands manage both projects together. Existing
conversations and volumes need no migration. Initial image/package builds require
downloads; the execution services have private internal runtime networks.

Open [Moyai](http://127.0.0.1:8787) and sign in as `admin`. Its generated password
is in the Git-ignored `.local/moyai/admin-password`. Upstream requires at least
12 characters. Existing LiteLLM/Grafana passwords are unaffected. The gateway key,
SSH identities and runtime configuration are private files under `.local/moyai/`.
Do not commit or paste these files into logs.

After startup, the Moyai entry in LiteLLM's workspace switcher opens this local
deployment. `make up` sets the supported LiteLLM UI setting `moyai_url` to
`http://127.0.0.1:8787` through its admin API, preserving the other UI settings.
Without that setting, LiteLLM 1.109.1 displays its default cloud setup page even
when a separate local Moyai is running. The link persists in the gateway database;
reload an already-open LiteLLM tab after connecting. The menu's upstream
description may still say “Cloud Coding Agent”; its target is the local URL.
See the [upstream UI setting](https://github.com/BerriAI/litellm/blob/v1.110.2/litellm/proxy/ui_crud_endpoints/proxy_setting_endpoints.py#L380).

The password file is an initial bootstrap credential, not an account-reset
mechanism. Keep it with the private configuration backup. If it is lost, startup
restores it from `runtime-config.json`; if both are missing while data remains,
startup fails with recovery guidance rather than generating an invalid new login.
Changing the file does not reset an existing account. Key preparation verifies
the actual virtual-key scope through LiteLLM; a replaced/revoked key causes only
the existing Moyai controller to reload its private configuration. The shared Moyai
inference key allows 10 concurrent requests and 60 requests per minute. Startup
updates the concurrency limit on an existing scoped key in place.

```sh
make status
make down
make restart
make moyai-status
make moyai-smoke
make moyai-smoke RESTART=1
```

`make down` stops Moyai first, then the core platform and all its profiles, then
unloads the configured coding and embedding models from memory without deleting
their artifacts. Files, history and volumes are preserved. `make restart` performs
the full stop/start cycle, including model unloading and automatic reloading when
inference resumes. `make status` lists both service groups and resident models.

The smoke test's `RESTART=1` option restarts only the Moyai services, so run it
when other Moyai tasks are idle. For maintenance, `make moyai-down` can stop only
the agent services; the core platform and its models remain available. Use
`make down` for complete shutdown and model memory release.

## What the MVP provides

- Authenticated upstream web chat and real streamed Responses tool calls.
- Native read/write/edit/list/grep and shell tools in a local nonroot sandbox.
- Python 3.14.10, pytest 9.2.0, Git and common terminal utilities available offline.
- Native `save_artifact` and authenticated downloads in Moyai's Files panel.
- Persistent application/Temporal state and per-conversation working directories.

For example, ask it to create `calc.py` and tests, run `python -m pytest -q`,
then call `save_artifact` for the program, tests and test report. Shell tool
results show stdout, stderr and exit status. The Files panel lists saved artifacts,
not every file in the live working directory. There is no separate interactive PTY.

This is a single-user coding MVP. Desktop/browser streaming, external service
connections and cloud filesystem snapshots are outside its verified scope.
Conversations have separate directories but share one sandbox OS user; this is
not a multi-tenant security boundary. Persistent files survive service restarts;
running processes and RAM do not. Native cloud-only panels/tools should not be
used with this SSH workspace.

## Local execution boundary

Moyai and Temporal share a private control network. Moyai and its SSH sandbox
share another private network; the sandbox cannot reach Temporal. The agent has
no Docker socket, host home, platform source checkout, model key or cloud account
credentials mounted into its workspace. Only `127.0.0.1:8787` is published.

A small unprivileged bridge has two fixed destinations: Moyai HTTP and the
existing LiteLLM service. It does not inspect request contents or accept a
client-selected upstream. The application's loopback relay satisfies upstream's
HTTPS-or-loopback gateway policy without exposing another host port.

The served web index uses local assets and system font fallbacks; upstream's
external Fontshare preconnect/stylesheet links are removed during image build.

Moyai uses a separate LiteLLM virtual key restricted to `local-qwen` and the
`/v1/responses` and `/model/info` routes. Live vector-search and MCP requests with
that key are denied. Repository context uses a distinct search-only credential
behind the internal bridge. The model profile explicitly sets `parallel_tool_calls=false` and `temperature=0`,
matching the local alias's declared capabilities. An initial default-profile run
returned textual tool markup; the sequential profile produced actual native tool
receipts and successful file/test/artifact results. The local adapter logs only
request metadata, not prompts, responses or credentials.

Tool generation remains model-dependent. The acceptance check rejects turns
that merely print tool markup or omit the required native artifacts; a completed
assistant message alone does not count as successful execution.

## Versions and verification

The Moyai runtime was verified on 2026-11-16. The gateway version was updated
and its local dashboard integration verified on 2026-11-20.

| Component | Pin |
| --- | --- |
| Moyai | `c375632aea66f6695b66158b5edf6ee4983dad72` |
| Official Moyai image | `sha256:05e9b95fdd96507b39784b9d8531758efdf2db30f0bceb03c161e2bd46b43419` |
| Pydantic AI / Harness | `2.55.0` / `0.55.0` |
| OpenAI SDK | `3.29.0` |
| Temporal CLI | `1.10.1`, pinned OCI digest in `compose.moyai.yaml` |
| Sandbox Python / pytest | `3.14.10` / `9.2.0` |
| LiteLLM | [`1.110.2`](https://github.com/BerriAI/litellm/releases/tag/v1.110.2), model alias `local-qwen` |

`make moyai-smoke RESTART=1` checks real native shell receipts with pytest exit
code 0, downloaded artifacts, the local LiteLLM UI link, blocked external execution egress, offline HTML,
loopback publishing, restart persistence and different conversation directories.
A trusted observer reads the controller's workspace registry and independently
checks the actual SSH filesystem and file hashes before any followup model turn. Its
metadata-only receipt is `.local/moyai/smoke-receipt.json`; application history and
artifacts remain in dedicated local volumes. No user request bodies are committed.

The platform suite includes PostgreSQL isolation and live gateway checks.
`make verify` runs the Moyai acceptance and shutdown cycle. [Platform operations](platform-operations.md)

Upstream references: [Moyai runtime injection](https://github.com/BerriAI/moyai/blob/c375632aea66f6695b66158b5edf6ee4983dad72/native/src/moyai/runtime.py#L124),
[native local-workspace tests](https://github.com/BerriAI/moyai/blob/c375632aea66f6695b66158b5edf6ee4983dad72/native/tests/conftest.py#L53),
[SSH workspace](https://github.com/pydantic/pydantic-ai/blob/v2.55.0/src/pydantic_ai_harness/pydantic_ai_harness/ssh_workspace/README.md),
[Temporal CLI](https://github.com/temporalio/cli/releases/tag/v1.10.1).
Moyai's MIT license is preserved in `integrations/moyai/LICENSE.upstream`.
