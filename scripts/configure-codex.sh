#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

load_env

mkdir -p .local/codex
temp=$(mktemp "${TMPDIR:-/tmp}/codex-config.XXXXXX")
sed "s/65536/$QWEN_CONTEXT_SIZE/g" codex/config.toml.template > "$temp"
mv "$temp" .local/codex/config.toml
chmod 600 .local/codex/config.toml
ok "Codex configuration generated at .local/codex/config.toml"
info "Use: source .local/client.env && CODEX_HOME=\"$ROOT_DIR/.local/codex\" codex"
info "VS Code: make vscode-install && make vscode"
