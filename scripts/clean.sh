#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

if [[ -d .local/logs ]]; then
  while IFS= read -r -d '' file; do
    : > "$file"
  done < <(find .local/logs -type f -print0)
fi

find retrieval -type f \( -name '*.pyc' -o -name '.coverage' \) -delete 2>/dev/null || true
find retrieval -type d -name __pycache__ -empty -delete 2>/dev/null || true
ok "Generated logs and Python bytecode cleared; keys, volumes, and models preserved"
