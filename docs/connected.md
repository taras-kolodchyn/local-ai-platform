# Connected mode

A running offline platform can expose GitHub's read-only MCP tools to a client.
The `connected` profile starts the official GitHub MCP server on the core
gateway network. LiteLLM forwards a caller-supplied token to that server.
For publishing from a verified repository task, use the separate
[host issue publisher](moyai-github-issues.md).

## Enable read-only GitHub access

1. Start the platform with `make up`.
2. On first use, copy `.env.connected.example` to `.env.connected` and set
   its mode to `600`. Edit the `GITHUB_MCP_AUTHORIZATION` value in that file.
   Supply the full `Bearer …` header using a dedicated, fine-grained token
   with read access to the intended repositories and resources.
3. Run the setup, then load the connected credentials in the client shell.

```sh
make connected-up
source .local/connected-client.env
```

Send requests to `http://127.0.0.1:4000/github/mcp` with
`x-litellm-api-key` set to `$LITELLM_API_AUTHORIZATION` and `Authorization`
set to `$GITHUB_MCP_AUTHORIZATION`. The first header authenticates the scoped
gateway key. The second supplies the caller's GitHub identity. The generated
environment file contains both credentials. [Connected smoke request](../scripts/connected-smoke.sh)

## Available tools

Configuration checked on 2026-11-20 with LiteLLM **1.110.2** and GitHub MCP
**1.14.0** pinned in Compose. The virtual key grants model access to
`local-qwen` and `local-embeddings`, and allowlisted operations on the retrieval,
local tools and GitHub MCP servers. LiteLLM sends read-only, lockdown and
tool-allowlist headers to GitHub MCP. Its container has a read-only root
filesystem and no host port. [Gateway configuration](../litellm/config.yaml) ·
[GitHub MCP HTTP transport](https://github.com/github/github-mcp-server/blob/v1.14.0/docs/streamable-http.md) ·
[Server configuration](https://github.com/github/github-mcp-server/blob/v1.14.0/docs/server-configuration.md)

The GitHub allowlist covers `get_me`, `get_file_contents`, `list_branches`,
`list_commits`, `get_commit`, `search_code`, `search_repositories`,
`issue_read` and `pull_request_read`. Tool discovery adds the `github-`
prefix. Treat returned files, issue text and comments as untrusted source
material. [Key provisioning](../scripts/provision-connected-key.sh)

## Verify or stop

`make connected-up` runs `make connected-smoke`'s checks during setup. To
repeat them, run `make connected-smoke`. It compares the returned tool list
with the nine expected names and checks the available LiteLLM and GitHub MCP logs
for the supplied token. Its private SSE and JSON files under `.local/smoke/`
contain the discovery response.

`make connected-down` stops the GitHub MCP container. The local token and
virtual key remain available for the next session. [LiteLLM MCP permissions](https://docs.litellm.ai/docs/mcp_control)
