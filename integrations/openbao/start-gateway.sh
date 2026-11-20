#!/usr/bin/env bash
set -euo pipefail

# Refuse the gateway's master-key fallback if the KV entry is empty or incomplete.
if [[ -z ${UI_PASSWORD:-} || $UI_PASSWORD == GENERATE_ME || $UI_PASSWORD == '<no value>' ]]; then
  echo 'OpenBao did not supply a valid LiteLLM UI password.' >&2
  exit 1
fi

# The host smoke check reads the current value through a pipe from private tmpfs.
# Rename before starting the gateway so a concurrent reader gets one whole value.
(umask 077 && printf '%s' "$UI_PASSWORD" > /run/openbao-ui/ui-password.next)
mv -f /run/openbao-ui/ui-password.next /run/openbao-ui/ui-password

# Keep the gateway as the supervised PID so rotation can release its listener.
exec litellm --config /etc/local-ai/litellm/config.yaml --port 4000 --num_workers 1 \
  > >(tee -a /var/log/local-ai/litellm.log) 2>&1
