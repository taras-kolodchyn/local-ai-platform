#!/usr/bin/env bash
set -euo pipefail
mkdir -p /run/sshd /workspace
cp /run/ssh-seed/host_ed25519 /run/sshd/host_ed25519
cp /run/ssh-seed/id_ed25519.pub /run/sshd/authorized_keys
chmod 600 /run/sshd/host_ed25519
chmod 644 /run/sshd/authorized_keys
chmod 1733 /tmp
python3 -I /opt/local-repository/session_accounts.py serve &
provisioner=$!
until [[ -S /run/session-accounts/control ]]; do
  kill -0 "$provisioner" || exit 1
  sleep 0.05
done
/usr/sbin/sshd -D -e &
listener=$!
trap 'kill "$listener" "$provisioner" 2>/dev/null || true' TERM INT EXIT
wait -n "$listener" "$provisioner"
exit 1
