#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

require_command python3

venv=.local/test-venv
if [[ ! -x "$venv/bin/python" ]]; then
  python3 -m venv "$venv"
fi

"$venv/bin/python" -m pip install --quiet --upgrade pip
"$venv/bin/python" -m pip install --quiet -e './retrieval[test]'
"$venv/bin/python" -m pip install --quiet -e './mcp/offline[test]'
"$venv/bin/python" -m pytest retrieval/tests
"$venv/bin/python" -m pytest mcp/offline/tests

if command -v cargo >/dev/null 2>&1; then
  cargo test --quiet --manifest-path examples/rust-service/Cargo.toml
else
  warn "cargo is unavailable; Rust demo test skipped"
fi

ok "Unit tests passed"
