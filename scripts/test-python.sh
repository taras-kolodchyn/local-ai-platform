#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

# Prefer the same minor version as CI, even when Homebrew's python3 advances.
if [[ -n "${PYTHON:-}" ]]; then
  candidates=("$PYTHON")
else
  candidates=(python3.14 python3.13 python3.12 python3.11 python3)
fi
for candidate in "${candidates[@]}"; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c \
    'import sys; sys.exit(not ((3, 11) <= sys.version_info[:2] < (3, 15)))' 2>/dev/null; then
    printf '%s\n' "$candidate"
    exit 0
  fi
done
die "Python 3.11–3.14 is required; install python@3.14 or set PYTHON to a supported interpreter"
