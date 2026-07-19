# Connected mode

Connected mode is an explicit opt-in layer over the default offline stack. It starts GitHub's official MCP server inside the `connected` Compose profile and routes it through the LiteLLM MCP Gateway. The GitHub container is not published to the host and receives the caller's token only on individual MCP requests.

## Enable read-only GitHub access

Create a dedicated fine-grained GitHub token that can read only the repositories and metadata needed for this workflow. Do not reuse an administrator token or the token used by `gh`.

```sh
cp .env.connected.example .env.connected
chmod 600 .env.connected
# Edit the placeholder without printing the token to the terminal.
make connected-up
```

`make connected-up` refuses the placeholder, starts only `github-mcp`, creates a separate LiteLLM virtual key with access to the three reviewed MCP servers, generates isolated Codex/Hermes configs, lists the exposed GitHub tools, rejects write-like tool names, and verifies that the authorization value did not enter service logs.

Run the connected Codex profile without changing the normal `~/.codex`:

```sh
source .local/connected-client.env
CODEX_HOME="$PWD/.local/codex-connected" codex
```

Stop the external tool without stopping the offline platform:

```sh
make connected-down
```

## Enforced boundaries

- `github-mcp` uses `ghcr.io/github/github-mcp-server:v1.6.0`, runs with a read-only root filesystem and no host port, and exists only in the `connected` profile.
- LiteLLM forwards the caller-specific `Authorization` header only to the `github` MCP server.
- Both LiteLLM and GitHub MCP enforce a named tool allowlist. GitHub MCP also receives its read-only and lockdown headers on every request.
- Connected clients use a different LiteLLM virtual key and configuration directory from offline clients.
- The GitHub token stays in ignored files under the user's control. It is not written to Compose environment variables, container metadata, checked-in YAML, or logs.

Read-only limits accidental mutation; it does not make external content trusted. Issues, pull requests, comments, and repository files can contain prompt injection. Treat their text as data, keep Codex approvals enabled, and review any shell or write action locally.

## Adding another external API

Use the same pattern: a separate Compose profile, an internal-only service, a narrow upstream tool allowlist, server-specific credential forwarding through LiteLLM, a distinct virtual key, and a smoke test that proves both permitted and denied behavior. Do not add an external server or its credential to the default key merely because the upstream supports read-only mode.

Official references:

- https://github.com/github/github-mcp-server/blob/v1.6.0/docs/streamable-http.md
- https://github.com/github/github-mcp-server/blob/v1.6.0/docs/server-configuration.md
- https://docs.litellm.ai/docs/mcp
