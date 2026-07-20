#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

readonly EXTENSION_ID="openai.chatgpt"
readonly CODEX_DIR="$ROOT_DIR/.local/codex"

find_code_cli() {
  local candidate

  if command -v code >/dev/null 2>&1; then
    command -v code
    return
  fi

  for candidate in \
    "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code" \
    "$HOME/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return
    fi
  done

  die "VS Code CLI was not found; install VS Code or add its 'code' command to PATH"
}

extension_version() {
  local code_cli=$1
  "$code_cli" --list-extensions --show-versions \
    | awk -F@ -v extension="$EXTENSION_ID" '$1 == extension { print $2; exit }'
}

find_bundled_codex() {
  local extensions_root candidate bundled=""

  for extensions_root in "$HOME/.vscode/extensions" "$HOME/.vscode-insiders/extensions"; do
    [[ -d "$extensions_root" ]] || continue
    while IFS= read -r candidate; do
      bundled=$candidate
    done < <(find "$extensions_root" -type f \
      -path '*/openai.chatgpt-*/bin/macos-aarch64/codex' -print | sort)
  done

  [[ -n "$bundled" && -x "$bundled" ]] \
    || die "The bundled Codex binary was not found; run make vscode-install"
  printf '%s\n' "$bundled"
}

require_generated_config() {
  [[ -s "$CODEX_DIR/config.toml" ]] \
    || die "Local Codex config is missing; run make up first"
  [[ -s "$CODEX_DIR/.env" ]] \
    || die "Local Codex credentials are missing; run make up first"

  local permissions
  if permissions=$(stat -f '%Lp' "$CODEX_DIR/.env" 2>/dev/null); then
    :
  else
    permissions=$(stat -c '%a' "$CODEX_DIR/.env")
  fi
  [[ "$permissions" == "600" ]] \
    || die "$CODEX_DIR/.env must have permissions 600 (found $permissions)"
}

run_check() {
  local code_cli=$1 version bundled doctor_json doctor_status

  require_command jq
  require_generated_config

  version=$(extension_version "$code_cli")
  [[ -n "$version" ]] \
    || die "Official Codex extension is not installed; run make vscode-install"
  bundled=$(find_bundled_codex)

  # Prove that the IDE's bundled runtime can load the key from CODEX_HOME/.env;
  # do not export the scoped key into the VS Code extension-host environment.
  set +e
  doctor_json=$(env -u LITELLM_API_KEY CODEX_HOME="$CODEX_DIR" \
    "$bundled" doctor --json)
  doctor_status=$?
  set -e
  [[ -n "$doctor_json" ]] \
    || die "Codex IDE runtime did not produce a doctor report (status $doctor_status)"

  jq -e '
    def ok(id): any(.checks[]?; .id == id and .status == "ok");
    ok("auth.credentials") and
    ok("config.load") and
    ok("mcp.config") and
    ok("network.provider_reachability")
  ' >/dev/null <<< "$doctor_json" \
    || die "Codex IDE runtime validation failed; run the bundled codex doctor for details"

  ok "VS Code $("$code_cli" --version | sed -n '1p') with $EXTENSION_ID $version"
  ok "$("$bundled" --version) loaded the local provider and MCP configuration"
  ok "Scoped credentials were loaded from CODEX_HOME/.env without shell export"
  if ((doctor_status != 0)); then
    info "Non-IDE doctor checks returned status $doctor_status; required IDE checks passed"
  fi
}

run_smoke() {
  local code_cli=$1 bundled turn_output exit_code agent_text

  run_check "$code_cli"
  bundled=$(find_bundled_codex)

  set +e
  turn_output=$(printf '%s\n' 'Do not use tools. Reply with exactly: VSCODE_LOCAL_OK' \
    | env -u LITELLM_API_KEY CODEX_HOME="$CODEX_DIR" \
      "$bundled" exec --strict-config --ephemeral --sandbox read-only \
      --json -C "$ROOT_DIR" - 2>&1)
  exit_code=$?
  set -e
  ((exit_code == 0)) \
    || die "Codex IDE runtime turn failed with status $exit_code"

  agent_text=$(jq -Rr '
    fromjson?
    | select(.type == "item.completed" and .item.type == "agent_message")
    | .item.text
  ' <<< "$turn_output")
  grep -Fxq 'VSCODE_LOCAL_OK' <<< "$agent_text" \
    || die "Codex IDE runtime did not return the expected response marker"
  if grep -Fq 'remote featured plugin request' <<< "$turn_output"; then
    die "Codex attempted to contact the remote plugin catalog"
  fi

  ok "Bundled Codex completed an ephemeral read-only turn through local LiteLLM"
  ok "No remote plugin-catalog request was emitted"
}

code_cli=$(find_code_cli)
action=${1:-launch}

case "$action" in
  install)
    "$code_cli" --install-extension "$EXTENSION_ID" --force
    version=$(extension_version "$code_cli")
    [[ -n "$version" ]] || die "VS Code did not report the installed Codex extension"
    ok "Installed $EXTENSION_ID $version"
    ;;
  check)
    run_check "$code_cli"
    ;;
  smoke)
    run_smoke "$code_cli"
    ;;
  launch)
    run_check "$code_cli"
    if pgrep -f '/Visual Studio Code.app/Contents/MacOS/Electron' >/dev/null 2>&1; then
      warn "VS Code is already running; fully quit it and rerun make vscode if Codex uses another CODEX_HOME"
    fi
    unset LITELLM_API_KEY LITELLM_API_AUTHORIZATION GITHUB_MCP_AUTHORIZATION
    export CODEX_HOME="$CODEX_DIR"
    info "Opening VS Code with isolated local Codex state"
    info "Use Command Palette -> Codex: Open Codex Sidebar, then keep execution Local"
    # Open a non-sensitive tracked file explicitly instead of letting VS Code
    # restore an ignored .env or another local secret as the active editor.
    exec "$code_cli" --new-window "$ROOT_DIR" "$ROOT_DIR/README.md"
    ;;
  *)
    die "Unknown action '$action'; use install, check, smoke, or launch"
    ;;
esac
