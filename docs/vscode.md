# Native VS Code workflow

Last verified: 2026-07-20 on Apple Silicon with VS Code `1.129.0`, the official
`openai.chatgpt` extension `26.715.31925`, and its bundled Codex
`0.145.0-alpha.18`.

The Codex IDE extension and CLI share the same configuration layers. `CODEX_HOME`
also applies to the IDE extension, so this repository can provide a native editor
experience without modifying `~/.codex/config.toml`. Official references:
[Codex IDE extension](https://developers.openai.com/codex/ide/),
[config basics](https://developers.openai.com/codex/config-basic/), and
[environment variables](https://developers.openai.com/codex/environment-variables/).

## First-time setup

Bootstrap the local platform, then install and validate the official extension:

```sh
make up
make vscode-install
make vscode-check
make vscode-smoke
```

`make vscode-install` uses VS Code's extension installer for the published
`openai.chatgpt` package. It does not install an alternate client or override
the extension's bundled Codex executable.

## Daily use

```sh
make up
make vscode
```

Then open the Command Palette with `Cmd+Shift+P`, run **Codex: Open Codex
Sidebar**, and keep the run on **Local**. Open files and selections can be added
to the prompt, edits appear as reviewable diffs, and the workspace-write sandbox
and on-request approvals remain active.

The repository also contributes VS Code tasks for `up`, `status`,
`vscode-check`, `vscode-smoke`, the platform `smoke-test`, and `down`. Run them
through **Tasks: Run Task**.

## Credential and network boundary

`make up` generates a scoped LiteLLM virtual key in
`.local/codex/.env`, sets permissions to `0600`, and keeps the entire `.local/`
tree ignored by Git. `make vscode` adds `CODEX_HOME` to the inherited editor
environment but does not export the scoped key or put it in
`.vscode/settings.json`. The Codex runtime loads the provider key from its own
local state directory.

The generated profile also:

- disables Codex web search;
- disables app/connector integrations, plugins, plugin sharing, and the remote
  plugin catalog;
- opts out of Codex analytics;
- excludes the LiteLLM key from model-generated child-process environments;
- retains `workspace-write` sandboxing and `on-request` approvals;
- exposes only the reviewed local MCP tool allowlists.

Selecting **Cloud** in the extension is a different execution boundary and is
not part of this local workflow. Installing or updating the extension requires
Internet access, but local model turns go to the loopback LiteLLM provider once
the platform and extension are present.

## What `make vscode-check` proves

The check locates the installed extension and its bundled Codex binary, removes
`LITELLM_API_KEY` from the shell environment, and runs the bundled runtime's
redacted doctor report with the isolated `CODEX_HOME`. It requires successful
checks for:

- custom-provider authentication from `CODEX_HOME/.env`;
- configuration parsing;
- MCP configuration consistency;
- local provider reachability.

This validates the native extension runtime path. It does not upgrade the local
model's autonomous coding quality beyond the results recorded in the
[capability matrix](capability-matrix.md).

`make vscode-smoke` adds one strict-config, ephemeral, read-only turn through
the extension's bundled Codex runtime. It requires the exact
`VSCODE_LOCAL_OK` response marker and fails if the runtime emits a remote
plugin-catalog request.

## Troubleshooting

If the sidebar shows an OpenAI model, asks for a ChatGPT sign-in, or cannot find
`LITELLM_API_KEY`, fully quit every VS Code window and relaunch with
`make vscode`. A VS Code process originally opened from Finder may retain a
different environment even when a later terminal command opens a new window.

Do not set `chatgpt.cliExecutable`; the extension documents that setting as
development-only. Do not add the local key to user or workspace settings. Run
`make vscode-check` and `make status` to distinguish IDE bootstrap problems from
an unavailable local gateway.
