# Troubleshooting

Start with:

```sh
make doctor
make status
docker compose ps
docker compose logs --tail=200 SERVICE
```

Generated keys are never printed by these commands.

## Docker Desktop UI is open but the daemon is unavailable

Symptom: `Cannot connect to the Docker daemon` while Docker Desktop processes exist.

Use the supported lifecycle commands first:

```sh
docker desktop status
docker desktop restart --timeout 240
```

If the restart cannot stop stale processes and `docker info` already fails, a forced application stop followed by a normal start does not delete images or volumes:

```sh
docker desktop stop --force --timeout 60
docker desktop start --timeout 240
docker info
```

Do not use Docker Desktop's factory reset for this symptom.

## Model Runner is unavailable

```sh
docker model version
docker desktop enable model-runner --tcp=12434
docker model status
docker model list
```

On macOS, the DMR engine is a host-sandboxed process, not a Linux container. An ordinary Compose service cannot gain Metal access by adding a device mapping.

## A model pull is interrupted

Run `make pull-model` again. DMR resumes or verifies content-addressed layers. Avoid deleting Docker data while diagnosing a partial pull.

## LiteLLM is unhealthy

```sh
docker compose logs --tail=200 litellm
docker compose config --quiet
curl -fsS http://127.0.0.1:4000/health/liveliness
```

Common causes are an invalid LiteLLM config, unavailable PostgreSQL/Redis, an unready DMR endpoint, or a model alias/artifact mismatch. Client names are `local-qwen` and `local-embeddings`; DMR artifact names should not leak into client config.

## Responses API chat works but tools do not

Confirm `use_chat_completions_api: true` remains on `local-qwen`, then run `make smoke-test`. A plain text completion is not proof of Responses tool compatibility. The smoke test forces a function call and sends the tool result back through the bridge.

For the second request, send only the `function_call_output` and the first
response's `previous_response_id`. Replaying the initial `function_call` caused
the Qwen llama.cpp template to fail while the previous-response sequence passed.

## A loopback port is configured but nothing is listening

Docker Desktop cannot publish a host port from a container attached only to
Compose networks marked `internal: true`. Keep the service's isolated network,
but also attach the narrow `mcp-host` or `observability-host` bridge used in this
repository. Confirm the effective bind rather than trusting Compose YAML:

```sh
docker compose port grafana 3000
lsof -nP -iTCP:3000 -sTCP:LISTEN
```

## MCP returns 421 from a Docker service name

The Python MCP SDK enables DNS-rebinding protection. The offline servers retain
that protection and explicitly allow their Compose hostnames (`retrieval:*` and
`mcp-tools:*`). Do not disable the guard globally. If you rename a service,
update the narrow allowed-host list and rerun `make smoke-test`.

LiteLLM's MCP route streams SSE and prefixes the remote tool name with the
registered server name. Clients should use names such as
`local_retrieval-search_code`, not assume the bare `search_code` name.

## Re-running `make up` reports a duplicate key alias

Provisioning is idempotent: it validates and reuses the existing local client
key. Do not create a second key with the same alias. If the local key files and
LiteLLM database were intentionally reset out of sync, use the guarded reset
workflow and bootstrap again instead of editing secrets by hand.

## A generated Hermes config contains an empty API-key expression

The container template intentionally preserves `${LITELLM_API_KEY}` for Hermes
to resolve at runtime. Shell generation must escape the dollar sign; expanding
it while `set -u` is active can abort setup before the client environment is
loaded.

## Embeddings fail or pgvector rejects a row

The selected Qwen embedding model must return 1024 finite values. Check the configured model, `EMBEDDING_DIMENSIONS`, and the database schema together; changing only one of them is not a migration.

## Retrieval returns stale content

Run the incremental command again:

```sh
make index REPO=/absolute/path
```

Use `make reindex REPO=/absolute/path` only when deliberately rebuilding that repository/branch snapshot. It does not remove other repositories.

## Grafana has no logs

Check `.local/logs`, Alloy, and Loki in that order. Alloy reads explicit metadata-only files and is intentionally not given the Docker socket:

```sh
ls -l .local/logs
docker compose logs --tail=100 alloy loki
```

## Connected GitHub MCP is unavailable

Connected mode is intentionally absent from `make up`. Verify that `.env.connected` contains a dedicated fine-grained read-only token, then run:

```sh
make connected-up
docker compose --profile connected ps github-mcp
make connected-smoke
```

Do not copy the token into an issue or logs. The smoke artifact contains tool names only and is written under ignored `.local/` storage.

## Resetting local data

`make clean` removes generated logs and Python bytecode but preserves keys, database volumes, and models. Full Compose-volume deletion is guarded:

```sh
make reset CONFIRM_RESET=yes
```

DMR model artifacts are preserved even by this repository reset.
