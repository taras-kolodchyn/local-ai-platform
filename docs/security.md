# Security boundaries

The platform runs under one trusted macOS account. Source checkouts, registered snapshots, conversations, generated files, credentials and model traffic all belong to that operator. Other processes running as the same macOS user can reach loopback services and read files accessible to that account. Keep the stack on a trusted Mac.

## Where data and authority cross boundaries

| Boundary | What the platform enforces | What the operator controls |
| --- | --- | --- |
| Mac and inference | Published ports bind to `127.0.0.1`. LiteLLM authenticates requests to the model aliases and Docker Model Runner serves inference on the Mac. | Keep the Model Runner listener on loopback. Local processes can contact it directly. |
| Checkout and index | Ingestion mounts the selected checkout read-only. The scanner filters sensitive paths and binary files. PostgreSQL stores selected source text, locations and embeddings. | Select the checkout to index and treat retrieved text, comments and tool output as untrusted input. A filename or content filter cannot identify every secret. |
| Local clients and tools | LiteLLM grants tools to scoped keys. Offline MCP provides bounded file and Git reads and a restricted SQL query. PostgreSQL also gives the MCP role read-only transactions and a statement timeout. | The retrieval and offline MCP services publish loopback endpoints of their own. Account access is part of the security boundary. |
| Moyai and its workspace | Moyai uses a scoped inference key and SSH to execute commands in a separate Linux sandbox. The sandbox has an internal network, a workspace volume and no Docker socket or writable host checkout. Conversations have distinct directories under one sandbox OS user. | Use analysis mode for read-only tasks. Review generated patches before applying them to a checkout. |
| Registered source and verified edits | The repository context bridge scopes searches to a registered snapshot and generation. The verifier checks an isolated working copy and delivers a patch and test evidence. | Choose the source and supported profile. Treat verified test results as evidence for that profile's checks. |
| GitHub | The optional connected MCP uses a separate token and read-only tool allowlist. The separate host publisher requires an explicit target and authorization to publish. | Select the GitHub repository and credential scope. A configured publisher can create a PR and its optional merge mode can update the target branch. |

[Core Compose services](../compose.yaml) · [Moyai networks and mounts](../compose.moyai.yaml) · [Read-only database role](../postgres/provision-mcp-role.sh) · [Connected MCP](connected.md) · [GitHub publisher](moyai-github-issues.md)

## Secrets, observability and stored content

Bootstrap creates secrets in the ignored `.env` and `.local/` paths. Clients receive scoped virtual keys. The master key is used for gateway administration and provisioning. Keep these files out of Git, reports and shared logs.

With `LITELLM_MASTER_KEY_SOURCE=openbao-local`, the gateway receives the master
key and database encryption salt from OpenBao Agent. Host provisioning reads the
current admin key on demand. Prometheus uses its own metrics-only virtual key.
[Migration and rotation](openbao.md#move-the-master-key-on-the-local-mac)

The optional OpenBao mode mounts one read-scoped AppRole into the gateway.
Its Agent injects the dashboard password into LiteLLM and the smoke check reads
the active value from private container memory. Use TLS to the OpenBao endpoint.
The AppRole and password are accessible to administrators of that container.
[OpenBao setup and rotation](openbao.md)

Service logging defaults to operational metadata. Moyai deliberately persists conversations and files. The knowledge index contains source text, and Redis can hold responses for explicitly cached requests. Treat the corresponding volumes and backups as private. Stopping the stack with `make down` preserves them.

The configuration reviewed on 2026-11-20 pins LiteLLM 1.110.2 and defaults `LITELLM_TURN_OFF_MESSAGE_LOGGING` to `true`. To deliberately record request and response content, change the setting to `false` in `.env`, apply it with `make up`, and enable **Store Prompts in Spend Logs** in the dashboard. Disabling storage affects future requests. [Gateway policy](../litellm/config.yaml) · [LiteLLM spend-log settings](https://docs.litellm.ai/docs/proxy/ui_spend_log_settings)

## Checking the boundaries

- `make test` exercises file and SQL restrictions, repository scoping, snapshot handling and database isolation.
- `make smoke-test` checks local listeners, dashboard logins, gateway credentials, retrieval access and synthetic prompt sentinels in collected logs.
- `make moyai-smoke` exercises an agent task, authenticated file delivery, workspace separation and sandbox egress.
- When read-only GitHub access is enabled, `make connected-smoke` checks the connected tool allowlist and log redaction.

[Private vulnerability reporting](../SECURITY.md)
