#!/usr/bin/env bash
set -euo pipefail
mkdir -p /run/sshd /workspace
cp /run/ssh-seed/host_ed25519 /run/sshd/host_ed25519
cp /run/ssh-seed/id_ed25519.pub /run/sshd/authorized_keys
chmod 600 /run/sshd/host_ed25519
chmod 644 /run/sshd/authorized_keys
chown agent:agent /workspace
exec /usr/sbin/sshd -D -e
