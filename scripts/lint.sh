#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

require_command docker
require_command python3

while IFS= read -r script; do
  bash -n "$script"
done < <(find scripts -type f -name '*.sh' -print | sort)
ok "Shell syntax valid"

if command -v shellcheck >/dev/null 2>&1; then
  # Each script resolves lib.sh from its own path; ShellCheck cannot follow a
  # dynamic source expression, so SC1091 is excluded explicitly.
  shellcheck --exclude=SC1091 scripts/*.sh
  ok "ShellCheck passed"
else
  warn "shellcheck is unavailable; syntax checks still ran"
fi

python3 -m compileall -q retrieval/src retrieval/tests mcp/offline/src mcp/offline/tests
python3 -m json.tool observability/grafana/dashboards/local-ai-platform.json >/dev/null
ok "Python and dashboard syntax valid"

bash scripts/bootstrap.sh >/dev/null
docker compose config --quiet
ok "Compose configuration valid"

if rg -n --hidden --glob '!LICENSE' --glob '!.git/**' \
  '(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----)' .; then
  die "Potential committed secret material found"
fi
ok "Repository secret-pattern check passed"
