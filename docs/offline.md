# Offline operation

The default offline stack publishes services only on host loopback. Local chat,
repository retrieval, the Moyai sandbox and dashboards use the resources on
the Mac. The GitHub MCP service and the host-side issue publisher require
separate opt-in commands.

## Prepare while connected

```sh
make up
```

Complete the first startup while connected. It pulls the two configured DMR
models, downloads and verifies the pinned MiniLM tokenizer and ARM64 ONNX weights,
and builds the core and Moyai images. The sandbox build installs its toolchain
dependencies and vendors the packages needed by the supported Rust verification
profile. Startup finishes by running the local smoke checks.
[Startup](../scripts/up.sh) · [Model preparation](../scripts/pull-model.sh) ·
[Pinned assets and versions](capability-matrix.md)

With the prepared stack running, disconnect the Mac and run `make smoke-test`
to exercise inference, retrieval, scoped tools, dashboard logins and monitoring.
Each invocation of `make up` calls `docker model pull` and Compose build steps.
These steps can require registry access even when a previous session ran locally.

## What remains local

- DMR serves generation and embeddings on macOS. Headroom compresses eligible
  inputs and MiniLM ranks source excerpts in private local services.
- PostgreSQL/pgvector stores the indexed text and embeddings. Moyai stores
  conversations and workspace files in named volumes. Keep `.env`, `.local/`
  and backups private.
- Host clients reach the core HTTP endpoints and the Moyai UI over loopback.
  Alloy sends local log files to Loki over an internal network. Loki has no
  published port. [Network and cache layout](architecture.md)

## Offline limitations

The offline profile controls configured providers and tools. Some container
networks permit outbound connections so services can reach the macOS host.
Use an air gap or a separate host network policy when egress must be blocked.
Other processes on the same Mac can reach published loopback services.
[Security design](security.md)

Use `docker compose --profile connected ps --status running github-mcp` to check
whether a previous session enabled GitHub MCP. Stop it with `make connected-down`.
Use `make down` to stop both projects and unload the two DMR models while
retaining the downloaded artifacts and persistent volumes. [Connected mode](connected.md)
