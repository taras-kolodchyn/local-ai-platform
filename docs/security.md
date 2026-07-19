# Security threat model

Last reviewed: 2026-07-19

## Overview

This repository assembles a single-user, local development platform for AI coding agents on Apple Silicon. Docker Model Runner (DMR) performs host-side inference; LiteLLM exposes authenticated OpenAI-compatible routes; PostgreSQL/pgvector and Redis hold retrieval and cache data; the retrieval and offline MCP services expose deliberately narrow, read-only tools; Prometheus, Loki, Alloy, and Grafana collect operational metadata. Codex runs on the host, while Hermes can run either on the host or in an optional container.

The primary assets are source code and uncommitted work, Git history, locally generated API/database credentials, agent tool authority, vectorized source fragments, model gateway traffic, and host integrity. The main production-like runtime surfaces are `compose.yaml`, `litellm/config.yaml`, `retrieval/`, `mcp/offline/`, `postgres/`, and the generated client configurations. `examples/rust-service/` is an intentionally vulnerable teaching fixture; its SQL interpolation, missing JWT validation, excessive retry cap, and weak deployment resources are not platform controls. `scripts/`, `.github/`, documentation, and tests are developer/CI surfaces, but can still affect supply-chain safety or generate unsafe configuration.

The design protects a trusted local operator from accidental or prompt-induced overreach. It is not a hardened multi-user service, a confidentiality boundary between processes running as the same macOS user, or a safe environment for unattended execution of arbitrary model-generated shell commands.

## Threat Model, Trust Boundaries, and Assumptions

### Actors and inputs

- **Attacker-controlled:** cloned repositories, dependencies, code comments, documentation, Git metadata, retrieved chunks, model output, MCP responses from connected services, and network input if a listener is exposed accidentally.
- **Operator-controlled:** selected repository paths, `.env` values, local credentials, enabled Compose profiles, Codex approvals, optional connected-mode configuration, retention settings, and any expansion of MCP tools or mounts.
- **Developer-controlled:** repository code, image/package/model pins, Compose networks and mounts, SQL migrations, tool schemas, allowlists, CI workflows, and the demo fixture.

Prompt injection is realistic whenever untrusted source or external content is indexed. A malicious repository author can try to make an agent reinterpret retrieved text as instructions. Remote unauthenticated attacks are out of scope only while every published port remains loopback-bound and DMR also listens on loopback. A malicious process running under the same macOS account remains able to reach localhost services and read files owned by that account; API keys reduce accidental cross-talk but do not create an OS-level isolation boundary.

### Trust boundaries

1. **Host user to containers.** Codex and DMR run on macOS; the application and data services run in Docker. Only selected host directories enter containers. The offline MCP workspace and ingestion source mount are read-only; no service receives the Docker socket.
2. **Client to LiteLLM.** Codex/Hermes cross an authenticated HTTP boundary using scoped virtual keys. DMR is behind LiteLLM for normal client traffic, although its loopback API is still directly reachable by local processes.
3. **Gateway to inference.** LiteLLM forwards prompts and tool schemas to DMR. DMR is a host sandbox using Apple GPU acceleration, not a Linux container controlled by Compose.
4. **Untrusted repository to retrieval.** The scanner accepts source-like files under an operator-selected root, excludes ignored, generated, binary, sensitive-path, and likely-secret content, chunks it, and stores embeddings. Retrieved text remains untrusted data.
5. **Agent to MCP tools.** MCP tool names and parameters are allowlisted. Retrieval exposes only `search_code` and `get_chunk`; the offline service exposes bounded filesystem/Git reads and a constrained PostgreSQL `SELECT` path using a separate read-only role.
6. **Services to telemetry.** Application metadata and service logs cross into Alloy/Loki/Prometheus/Grafana. Prompt, response, authorization, and retrieved-source bodies are excluded by policy because observability storage broadens the disclosure surface.
7. **Offline to connected mode.** The default stack has no external MCP credentials or running remote tool. `make connected-up` starts the profile-scoped GitHub MCP service and introduces a new Internet and authorization boundary with a separate scoped token, LiteLLM virtual key, tool allowlist, and client configuration.

Assumptions: the operator controls the Mac and Docker Desktop; local images and packages were obtained from their stated upstreams; host disk encryption and login protections are outside this repository; the selected workspace may be hostile; and approvals are reviewed by a human. Multi-user hosting, Internet exposure, secrets management for a team, and autonomous production changes are out of scope.

## Attack Surface, Mitigations, and Attacker Stories

### Retrieval prompt injection to tool execution

An attacker commits text that asks the model to ignore policy, disclose files, or run a destructive command. Retrieval returns it, and the agent may treat it as instruction. `retrieval/src/local_ai_retrieval/scanner.py` constrains what is indexed, while the retrieval server returns bounded, attributed chunks and has no mutation tool. `codex/config.toml.template` keeps the client in `workspace-write` with `on-request` approval; the offline MCP server remains read-only. These controls reduce blast radius but cannot guarantee that a local model will follow instruction hierarchy, so high-impact commands still require human inspection.

### Excessive MCP or database authority

A broad filesystem, shell, Docker, GitHub, or database tool could turn one model error into host or external-system compromise. `compose.yaml` mounts the selected MCP workspace read-only, does not mount `/var/run/docker.sock`, and isolates services across gateway, data, and observability networks. `postgres/provision-mcp-role.sh` creates `mcp_reader` with `default_transaction_read_only`, `SELECT` grants, and a statement timeout. `mcp/offline/` also validates paths, tool names, result sizes, and `SELECT`-only SQL. Connected-mode write tools are deliberately absent from the default profile.

### Path traversal and oversized reads

A crafted tool argument can attempt `../` traversal, symlink escape, or resource exhaustion through large files, Git output, SQL rows, retrieval limits, or embedding batches. The MCP implementation resolves requested paths under the configured root and enforces extensions/size limits. Retrieval caps file size, chunk size, top-k, and returned characters. PostgreSQL calls have row and time limits. Tests should include traversal, symlink, non-`SELECT`, multiple-statement, and over-limit cases; path validation must occur after canonicalization.

### Credential or source leakage through telemetry

Authorization headers, prompts, tool arguments, retrieved code, or database URLs could reach container output, LiteLLM spend logs, Loki, dashboards, smoke artifacts, or CI. Secrets are generated into ignored `.local/` files with restrictive permissions and are not echoed. Compose secrets carry the LiteLLM client key to retrieval; client keys are distinct from the master key. Logging is metadata-only, and `scripts/smoke-test.sh` searches collected logs for synthetic prompt sentinels. This is a regression control, not proof that every future error path redacts correctly.

### Local gateway exposure and authentication bypass

If a host port changes from `127.0.0.1` to `0.0.0.0`, another LAN host could call the gateway, enumerate tools, or consume inference resources. Every published port in `compose.yaml` is explicitly loopback-bound, and the smoke test inspects listeners. LiteLLM requires keys, and DMR is configured for loopback. Localhost still permits other processes under the host user to connect, so secrets should not be placed in prompts and this stack should not be used on an untrusted shared account.

### Supply-chain and configuration compromise

Containers, Python/Rust packages, models, GitHub Actions, and optional MCP servers execute trusted code. Versions are pinned and the platform validates arm64 compatibility; release evidence should also record immutable image/model digests. Tags can be retargeted, so upgrades need review and scanning. A compromised developer script or CI workflow can also exfiltrate repository secrets even if the runtime is safe. The intentionally flawed demo must never be mistaken for a deployment template.

### Denial of service and resource pressure

Large repositories, adversarial queries, excessive context windows, cache growth, log retention, and concurrent model calls can exhaust the Mac's unified memory or disk. Compose sets service memory limits where applicable; ingestion and retrieval are bounded; Prometheus retention is finite; and model pull/doctor scripts check available resources. DMR itself is outside Compose resource enforcement, so the operator must still monitor unified memory, Docker disk allocation, and model storage.

### Existing validation controls

- `scripts/smoke-test.sh` checks authenticated model discovery, embeddings, chat, Responses tool round trips, streaming, explicit caching, RAG, MCP discovery, listener scope, telemetry health, and prompt-sentinel absence.
- `mcp/offline/tests/` and `retrieval/tests/` exercise core policy and retrieval behavior; CI also runs Rust tests for the teaching fixture.
- Generated keys and runtime artifacts stay under ignored `.local/`; destructive cleanup requires explicit confirmation.
- `SECURITY.md` asks reporters to use private vulnerability reporting and avoid including real credentials or source.

## Severity Calibration (Critical, High, Medium, Low)

### Critical

- Default `make up` exposes a no-approval arbitrary command or arbitrary host-file write primitive to the LAN/Internet, enabling host compromise with no operator action.
- A shipped connected-mode credential grants organization-wide destructive access and can be extracted by an untrusted repository or unauthenticated caller.

These require a reachable attacker and a direct high-impact capability. They are not realistic under the documented single-user, loopback-only, offline default unless those controls are bypassed or misconfigured.

### High

- Path/symlink traversal in a default MCP read tool lets a malicious repository retrieve SSH keys, cloud credentials, or private source outside the selected workspace.
- Prompt or tool data is persistently logged with active LiteLLM/GitHub credentials, or the MCP database role can write/drop tables despite its read-only contract.
- A default service mounts the Docker socket or a writable host root, allowing prompt-induced container/host takeover after one approved-looking call.

### Medium

- An authenticated local caller bypasses result-size or statement-timeout limits and reliably exhausts memory, disk, or database connections.
- Cache keys incorrectly cross virtual-key or model boundaries and return one local workflow's response to another local client.
- Metadata logs disclose repository names, paths, or query summaries beyond the stated policy without exposing active secrets or host-control primitives.

### Low

- A loopback health or metrics endpoint reveals component versions or availability on a trusted, single-user Mac.
- The demo service's deliberate SQL interpolation, absent JWT validation, or weak Helm resources are reported as platform vulnerabilities even though `examples/rust-service/` is explicitly non-production and not started by the default stack.
- A denial-of-service requires the trusted operator to opt into unusually large models or intentionally remove documented limits.

Severity should be lowered when exploitation requires a trusted operator to replace loopback bindings, enable a connected profile, broaden mounts/tools, and approve the harmful action. It should be raised when a default, unauthenticated path crosses the host, credential, repository, or external-service boundary without meaningful user interaction.
