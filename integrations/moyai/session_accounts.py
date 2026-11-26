"""Provision one unprivileged SSH login per controller-selected workspace."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import sys

ROOT = Path('/workspace')
RUN = Path('/run/session-accounts')
SOCKET = RUN / 'control'
CONTROL_UID = 10001
FIRST_UID = 200000
KEY = re.compile('[a-f0-9]{64}')


def restrict_legacy_tree(path, uid):
    # The root-owned parent prevents concurrent replacement by a session. Keep
    # the guard closed until every entry belongs to the new identity.
    path.chmod(0o700)
    os.chown(path, 0, 0)
    linked = {}
    for _, dirs, files, fd in os.fwalk(path):
        for name in files + dirs:
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) and info.st_nlink > 1:
                inode = (info.st_dev, info.st_ino)
                linked[inode] = linked.get(inode, info.st_nlink) - 1
    if any(linked.values()):
        raise ValueError('Legacy checkout contains a link shared outside its directory.')
    for _, dirs, files, fd in os.fwalk(path):
        for name in files + dirs:
            os.chown(name, uid, uid, dir_fd=fd, follow_symlinks=False)
    os.chown(path, 0, uid)
    path.chmod(0o770)


def checkout_uid(registry, key):
    # A root-only claim reserves this UID even after its checkout is removed.
    uid = FIRST_UID + 1 + int(key[:7], 16)
    while True:
        claim = registry / str(uid)
        try:
            with claim.open('x') as handle:
                handle.write(key)
                handle.flush()
                os.fsync(handle.fileno())
            return uid
        except FileExistsError:
            if claim.read_text() == key:
                return uid
            uid += 1


def account(registry, key, create):
    if not KEY.fullmatch(key):
        raise ValueError('Invalid workspace identifier.')
    path = ROOT / key
    try:
        info = path.lstat()
    except FileNotFoundError:
        if create:
            path.mkdir(mode=0o700)
        info = None
    if info and not stat.S_ISDIR(info.st_mode):
        raise ValueError('Expected a workspace directory.')
    uid = checkout_uid(registry, key)
    if info or create:
        if not info or info.st_uid != 0 or info.st_gid != uid or stat.S_IMODE(info.st_mode) != 0o770:
            if info and info.st_uid != CONTROL_UID and (info.st_uid, info.st_gid) not in {(0, 0), (0, uid)}:
                raise ValueError('Unexpected checkout owner.')
            restrict_legacy_tree(path, uid)
    name = 'w' + hashlib.sha256(key.encode()).hexdigest()
    home = RUN / 'homes' / name
    if not home.exists():
        home.mkdir(mode=0o700)
        (home / '.gitconfig').write_text(f'[safe]\n\tdirectory = {path}\n')
        os.chown(home / '.gitconfig', uid, uid)
        os.chown(home, 0, uid)
        home.chmod(0o770)
    passwd = RUN / 'passwd'
    entry = f'{name}:x:{uid}:{uid}:Private workspace:{home}:/bin/sh\n'
    contents = passwd.read_text()
    if entry not in contents.splitlines(keepends=True):
        temporary = RUN / 'passwd.new'
        temporary.write_text(contents + entry)
        temporary.chmod(0o644)
        temporary.replace(passwd)
    return {'user': name, 'exists': bool(info or create)}


def serve():
    RUN.mkdir(mode=0o711, exist_ok=True)
    (RUN / 'homes').mkdir(mode=0o711, exist_ok=True)
    passwd = RUN / 'passwd'
    passwd.write_bytes(Path('/opt/local-repository/base-passwd').read_bytes())
    passwd.chmod(0o644)
    # Old checkouts may have mode 0755. Close these before sshd accepts logins.
    os.chown(ROOT, 0, CONTROL_UID)
    ROOT.chmod(0o771)
    for path in ROOT.iterdir():
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode) and info.st_uid == CONTROL_UID:
            path.chmod(0o700)
    registry = ROOT / '.session-accounts'
    registry.mkdir(mode=0o700, exist_ok=True)
    info = registry.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Invalid account registry.')
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as listener:
        listener.bind(str(SOCKET))
        os.chown(SOCKET, CONTROL_UID, CONTROL_UID)
        SOCKET.chmod(0o600)
        listener.listen()
        while True:
            conn, _ = listener.accept()
            with conn:
                try:
                    _, peer, _ = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if peer not in (0, CONTROL_UID):
                        raise PermissionError('Untrusted provisioning caller.')
                    conn.settimeout(5)
                    mode, key = conn.recv(128).decode('ascii').split()
                    if mode not in ('open', 'lookup'):
                        raise ValueError('Unknown account operation.')
                    reply = account(registry, key, mode == 'open')
                except (ValueError, OSError) as exc:
                    print('Checkout provisioning failed:', type(exc).__name__, file=sys.stderr, flush=True)
                    reply = {'error': 'Private checkout unavailable.'}
                try:
                    conn.sendall(json.dumps(reply).encode())
                except OSError:
                    pass


if __name__ == '__main__':
    if sys.argv[1:] == ['serve']:
        serve()
    else:
        mode, key = sys.argv[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
            client.settimeout(60)
            client.connect(str(SOCKET))
            client.sendall(f'{mode} {key}'.encode())
            sys.stdout.buffer.write(client.recv(512))
