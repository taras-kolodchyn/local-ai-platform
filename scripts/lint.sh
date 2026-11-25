#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

shell_roots=(scripts integrations postgres)
find "${shell_roots[@]}" -type f -name '*.sh' -print0 | xargs -0 -n 1 bash -n --
ok "Shell syntax valid"

if command -v shellcheck >/dev/null 2>&1; then
  # Each script resolves lib.sh from its own path; ShellCheck cannot follow a
  # dynamic source expression, so SC1091 is excluded explicitly.
  find "${shell_roots[@]}" -type f -name '*.sh' -exec shellcheck --exclude=SC1091 -- {} +
  ok "ShellCheck passed"
else
  warn "shellcheck is unavailable; syntax checks still ran"
fi

if [[ ${1:-} == --shell-only ]]; then
  exit 0
fi

require_command docker
require_command python3

python3 -m compileall -q retrieval/src retrieval/tests mcp/offline/src mcp/offline/tests execution/src execution/tests integrations/moyai scripts/*.py
python3 -m json.tool observability/grafana/dashboards/local-ai-platform.json >/dev/null
ok "Python syntax and dashboard JSON valid"

bash scripts/bootstrap.sh >/dev/null
load_env
docker compose config --quiet
docker compose -f compose.yaml -f compose.mcp-current.yaml config --quiet
docker compose -f compose.moyai.yaml config --quiet
docker compose -f compose.moyai.yaml -f compose.moyai-volume-current.yaml config --quiet
docker compose -f compose.moyai.yaml -f compose.moyai-github.yaml -f compose.moyai-volume-current.yaml config --quiet
ok "Compose configuration valid"

if rg -n --hidden --glob '!LICENSE' --glob '!.git/**' \
  '(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----)' .; then
  die "Potential committed secret material found"
fi
ok "Repository secret-pattern check passed"
