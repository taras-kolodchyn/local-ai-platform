# Offline operation

The default runtime starts no external MCP service and publishes services only on host loopback. The GitHub MCP definition is inert because its Compose profile is disabled and the default virtual key does not grant access to it. After the offline images and both model artifacts have been pulled, chat, embeddings, indexing, retrieval, dashboards, and logs work without an internet connection.

Here, "offline" means no configured remote provider/tool and full operation
while the Mac has no Internet connection. It is not a packet-filtering claim:
the Docker bridge used to reach host-side Model Runner and publish loopback
ports is not `internal`, so a compromised container must still be treated as
having possible egress. Enforce host firewall/air-gap policy separately when
network-level denial is required.

## Prepare while connected

```sh
make pull-model
docker compose pull
docker compose build retrieval
make vscode-install
make up
make smoke-test
```

For an auditable offline transfer, record image and model digests on the connected Mac, export container images with `docker image save`, and follow Docker Model Runner's supported model packaging flow. Do not copy Docker Desktop's internal disk image while it is running.

## What remains local

- Model inference runs through Docker Model Runner on the Mac.
- Prompts reach LiteLLM and DMR but no configured remote provider.
- Embeddings and indexed chunks stay in the local Compose volumes.
- Grafana, Prometheus, Loki, and retrieval bind to `127.0.0.1`.
- `.env`, virtual keys, smoke artifacts, logs, and generated agent configs stay under ignored local paths.
- The Codex IDE profile disables web search, app/connector and plugin features, plugin sharing, the remote plugin catalog, and Codex analytics; local turns use the loopback LiteLLM provider.

## Offline limitations

- The first model/image pull and dependency installation require connectivity.
- Installing or updating the VS Code Codex extension requires connectivity; selecting its Cloud execution path is not an offline run.
- The GitHub Actions workflow is a connected CI check; it does not run the Metal-backed integration suite.
- Connected MCP, web search, package lookup, and repository hosting are unavailable.
- A local process running as the same macOS user may still reach loopback services; offline is not a multi-user authorization boundary.
- The default profile does not configure external calls, but it does not enforce
  an egress firewall for every container.

To confirm the external service is absent, run `docker compose ps github-mcp`; it must not be running unless connected mode was explicitly enabled. To stop all Compose services while preserving data and model artifacts, run `make down`.
