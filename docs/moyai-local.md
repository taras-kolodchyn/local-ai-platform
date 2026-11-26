# Local Moyai

Moyai runs the local coding agent. Its web app, API and Temporal workflow live in
the `local-ai-moyai` Compose project. The controller sends inference requests to
the `local-qwen` LiteLLM alias. Docker Model Runner runs inference with Metal on
the Mac. Shell commands and file edits run over SSH in a separate Linux sandbox.
[Compose services and mounts](../compose.moyai.yaml) · [Docker inference engines](https://docs.docker.com/ai/model-runner/inference-engines/)

## Start a chat

Run `make up` and sign in as `admin` with the generated password in
`.local/moyai/admin-password`. The **Moyai** entry in the LiteLLM workspace
switcher also opens this instance. [Open local Moyai](http://127.0.0.1:8787)

For a first task, ask Moyai to create a Python module and its tests, run
`python -m pytest -q`, and call `save_artifact` for the module, tests and test
report. Look at the shell receipt for the exit code. The **Files** panel lists
the artifacts the agent explicitly saved and provides authenticated downloads.
The conversation's working directory, saved files and history survive restarts.
Running processes end when the sandbox stops.

Choose **local-qwen-analysis** in the model picker to discuss code, plans or
architecture in chat. This profile uses the same gateway alias, can read and list
existing workspace files and can load text skills. Choose **local-qwen** for
shell commands, edits and saved artifacts. A web message beginning with
`/analysis` also selects analysis mode. [Analysis profile](../integrations/moyai/analysis_mode.py)

## Skills and context

Startup seeds 25 text workflows from the packaged catalog into **Skills** in the
LiteLLM dashboard. Moyai pins the enabled catalog for each turn. Qwen selects the
guides it needs by summary and loads their text through `load_capability`.
A guide supplies instructions within the current tool permissions.
[Packaged workflows and license](../integrations/moyai/skills) · [LiteLLM marketplace](https://docs.litellm.ai/docs/tutorials/claude_code_plugin_marketplace)

To edit a guide, keep its one-line summary and Markdown instructions in the
**Description** field, separated by a blank line, and keep the `moyai-text-v1`
keyword. Use **Enabled** to change its availability. The next turn picks up the
new catalog. Startup preserves operator-edited descriptions. The dashboard entry
`addyosmani-test-driven-development` appears in chat as `test-driven-development`.
[Catalog provisioning](../scripts/provision-workspace.py)

Moyai gives each agent request a working context window of one quarter of the
configured `QWEN_CONTEXT_SIZE`, or 16,384 tokens with the default of 65,536.
The agent can summarize older history. LiteLLM can also compress eligible context
at the gateway. The repository search tool supplies excerpts from the snapshot
attached to the conversation. [Inference configuration](../integrations/moyai/inference.py) ·
[Repository search and citations](repository-workflow.md#source-and-citations)

## Access and recovery

Only the Moyai web port, `127.0.0.1:8787`, is published from its Compose project.
The controller and sandbox share an internal network. The sandbox has its own
workspace volume. The controller selects a separate unprivileged SSH login for
each conversation and connection scope. Linux directory permissions isolate
their checkouts within the shared sandbox. The Mac remains under the control
of one trusted operator. The sandbox has no host home,
Docker socket, writable host checkout or LiteLLM credentials mounted into it.

The controller's inference key is scoped to `local-qwen`, `/v1/responses` and
`/model/info`. The separate repository context service holds its own scoped
search credential. A fixed-destination bridge connects Moyai to the local
gateway. [Key policy](../integrations/moyai/local_config.py) · [Relay](../integrations/moyai/relay.py)

Back up the private `.local/moyai/` configuration alongside the Moyai data
volumes. The admin password is a bootstrap credential. If the password file is
lost, startup recovers it from `runtime-config.json`. If both are lost while
Moyai data is retained, restore one from backup. Editing the file does not reset
an existing account. [Credential preparation](../scripts/moyai-prepare.py)

## Check the sandbox

Run `make moyai-smoke` against the running stack. It creates real conversations
and checks the pytest exit code, downloaded artifact hashes, sandbox files,
blocked external access, and separation of conversation directories. Add
`RESTART=1` when other tasks are idle to check persistence across a Moyai service
restart. The receipt is written to `.local/moyai/smoke-receipt.json`.
[Acceptance checks](../scripts/moyai-smoke.py)

`make moyai-status` lists just the agent services. `make moyai-down` stops those
services. Use `make down` for a full shutdown and model unload.
[Platform operations](platform-operations.md)

The checked-in configuration on 2026-11-20 pins LiteLLM 1.110.2, Temporal 1.10.1
and the Moyai image at
`sha256:05e9b95fdd96507b39784b9d8531758efdf2db30f0bceb03c161e2bd46b43419`.
The sandbox build selects Rust 1.100.0, Python 3.14.10 and pytest 9.2.0.
[Gateway image](../compose.yaml) · [Controller image](../integrations/moyai/Dockerfile) ·
[Sandbox build](../integrations/moyai/Dockerfile.sandbox)
