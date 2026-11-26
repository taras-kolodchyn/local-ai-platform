# Troubleshooting

Run these from the checkout used for `make up`. The two Compose projects have
separate container and log inventories.

```sh
make doctor
make status
docker compose ps -a
make moyai-status
```

## Docker or Model Runner is unavailable

Check the daemon and the runner separately. `docker model version` can print a
client version even when its server is unreachable.

```sh
docker info
docker model version
docker model status
# Docker Desktop (macOS): docker desktop status
# Docker Engine (Linux): systemctl status docker
```

Start Docker if needed — Docker Desktop on macOS, or `sudo systemctl start docker`
on Linux (your user must be in the `docker` group). On Docker Engine the Model
Runner TCP port 12434 is enabled by default; install the plugin with
`sudo apt-get install docker-model-plugin` if `docker model version` fails. Once
the daemon and runner are reachable, `make pull-model` enables host TCP on port
12434 on Docker Desktop, pulls the configured DMR models, verifies the MiniLM
files and applies the model configuration. Re-run it after an interrupted pull,
then start the platform with `make up`.
[DMR setup](https://docs.docker.com/ai/model-runner/get-started/) ·
[Model preparation](../scripts/pull-model.sh)

## Containers stopped but models still occupy memory

`docker compose down` stops only the selected project. Use `make down` to
stop both projects and unload the two configured DMR models. Check
`docker model ps` for residency. `docker model list` includes model artifacts on
disk. If shutdown reports a failure, fix the reported service or runner error
and retry `make down`. [Shutdown implementation](../scripts/down.sh) ·
[Docker model unload](https://docs.docker.com/reference/cli/docker/model/unload/)

## The gateway is unhealthy or an alias fails

```sh
docker compose config --quiet
docker compose ps -a litellm postgres redis reranker
docker compose logs --tail=100 litellm reranker
curl -fsS http://127.0.0.1:4000/health/liveliness
```

LiteLLM waits for healthy PostgreSQL, Redis and reranker services. If MiniLM
fails to load its model or tokenizer, repeat `make pull-model` to restore the
pinned files. If gateway liveness succeeds but chat or embeddings fail, check
the DMR server and resolved references with `make status FORMAT=json`. Run
`make up` after changing `.env` to render and load the active routing config,
then use `make smoke-test` to check model bindings, output and dashboard access.
[Capability and version matrix](capability-matrix.md)

The analysis choice in Moyai's model picker is an agent profile.
OpenAI-compatible clients send `local-qwen` for generation,
`local-embeddings` for embeddings or `local-reranker` for ranking.
[Moyai model profiles](moyai-local.md)

## Chat works but a Responses tool result fails

The gateway template sets `use_chat_completions_api: true` on `local-qwen`.
For the follow-up request, set `previous_response_id` to the first response's
`id` and send the `function_call_output` item with its original `call_id`. The smoke suite
checks the whole round trip and separately checks a streamed Responses call.
[Working request shapes](../scripts/smoke-test.sh) ·
[LiteLLM Responses bridge](https://docs.litellm.ai/docs/response_api)

## A dashboard rejects the configured login

Check `UI_USERNAME` (default `admin`) and `UI_PASSWORD` in `.env`. An empty
password uses `LITELLM_MASTER_KEY`. After editing the file, run `make up` and
sign in to LiteLLM in a fresh browser session.

Grafana's initial admin password is persisted in its volume.
Changing `GRAFANA_ADMIN_PASSWORD` on an existing installation can leave the
stored password out of sync. Follow the password-update command and re-run
`make smoke-test` to verify fresh sessions.
[Dashboard login procedure](../README.md#shared-dashboard-login-optional)

## A loopback port is unreachable or MCP returns 421

Compare the effective host binding and listener. For example, run
`docker compose port grafana 3000` and `lsof -nP -iTCP:3000 -sTCP:LISTEN`.
The core configuration attaches Grafana and Prometheus to the
`observability-host` bridge and offline MCP to `mcp-host`. The Moyai UI
uses its own bridge on port 8787. [Network layout](architecture.md#deployment-and-observability)

HTTP 421 from an MCP service can indicate a host outside its DNS-rebinding
allowlist. Check the service URL in LiteLLM and the allowed hosts on the
destination. Use the tool names returned by `tools/list` through LiteLLM,
for example `local_retrieval-search_code`. [Gateway MCP configuration](../litellm/config.yaml)

## Retrieval returns stale or incompatible results

`make index REPO=/absolute/path` incrementally refreshes the repository/branch
index. A Moyai repository chat stays on its attached snapshot and indexed
generation. To work on new source, register the updated checkout and attach the
new version to a new task. A changed embedding artifact can also require fresh
registration or reindexing. The default schema uses vectors of 1,024 dimensions.
[Search and scoring](platform-operations.md#hybrid-retrieval) ·
[Repository tasks](repository-workflow.md)

## Startup reaches MCP checks but monitoring is not ready

Run `bash scripts/check-observability.sh`. The default deadline is 60 seconds
and Prometheus scrapes every 15 seconds. The check names the failing endpoint
or reports the last scrape error. If `repository-context:8000` is missing, run
`make moyai-status` and inspect the context service with
`docker compose -f compose.moyai.yaml logs --tail=100 repository-context`.
The context service must join the core observability network. `make up`
checks that service again after provisioning the Moyai UI.
[Scrape configuration](../observability/prometheus/prometheus.yml) ·
[Readiness probe](../scripts/check-observability.sh)

For missing Grafana logs, check that `.local/logs/` contains service log
files, then inspect `docker compose logs --tail=100 alloy loki`. Alloy tails
the mounted log directory. [Alloy configuration](../observability/alloy/config.alloy)

## GitHub MCP is unavailable

Start the local platform, then follow the token and client-header steps in
the connected-mode guide. `make connected-smoke` checks the nine advertised
tools and log redaction. [Connected-mode runbook](connected.md)

## A queued GitHub write fails after confirmation

Check the saved turn and GitHub for the actual branch, PR or review before
retrying. Check the installation token's expiration and renew it if needed.
Use a fresh private chat with the updated connection revision.
[Queue recovery](moyai-github-issues.md#recover-a-queued-issue)

## A host Python upgrade breaks the tests

`make test` uses Python 3.11–3.14 and prefers 3.14. To select an installed
supported interpreter, run `PYTHON=/opt/homebrew/bin/python3.14 make test`.
The script recreates `.local/test-venv` if it contains an unsupported Python.
[Test interpreter selection](../scripts/test-python.sh)

## Resetting local data

`make clean` truncates the core service log files and clears generated Python
bytecode and coverage files under `retrieval/`. It preserves the volumes, keys
and model artifacts. `make reset CONFIRM_RESET=yes` stops both projects and
**deletes the core data volumes**, including the gateway database, knowledge
index, Redis and monitoring data. Moyai's own volumes and the downloaded models
are retained. Rebuild the index and registrations after a core reset.
[Lifecycle targets](../Makefile)
