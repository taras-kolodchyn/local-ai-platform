#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

load_env
[[ -s .local/connected-client.env ]] || die "Connected client environment is missing"

mkdir -p .local/codex-connected .local/hermes-connected .local/hermes-container
chmod 700 .local/codex-connected .local/hermes-connected .local/hermes-container

codex_temp=$(mktemp "${TMPDIR:-/tmp}/codex-connected.XXXXXX")
hermes_temp=$(mktemp "${TMPDIR:-/tmp}/hermes-connected.XXXXXX")
container_temp=$(mktemp "${TMPDIR:-/tmp}/hermes-container-connected.XXXXXX")
trap 'rm -f "$codex_temp" "$hermes_temp" "$container_temp"' EXIT

sed "s/65536/$QWEN_CONTEXT_SIZE/g" codex/config.connected.toml.template > "$codex_temp"
sed "s/65536/$QWEN_CONTEXT_SIZE/g" hermes/config.connected.example.yaml > "$hermes_temp"
sed -e "s/65536/$QWEN_CONTEXT_SIZE/g" -e 's#127\.0\.0\.1:4000#litellm:4000#g' \
  hermes/config.connected.example.yaml > "$container_temp"

mv "$codex_temp" .local/codex-connected/config.toml
mv "$hermes_temp" .local/hermes-connected/config.yaml
mv "$container_temp" .local/hermes-container/config.yaml
chmod 600 .local/codex-connected/config.toml .local/hermes-connected/config.yaml .local/hermes-container/config.yaml
ok "Connected Codex and Hermes configurations generated under .local/"
