"""Linux subprocess for one verification command. Its private socket belongs to the guest."""
import ctypes
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import time


PR_SET_CHILD_SUBREAPER = 36
WAIT_ALL = 0x40000000  # Linux __WALL includes children with alternate exit signals.
PEEK_CHILD = os.WEXITED | os.WNOHANG | os.WNOWAIT | WAIT_ALL
CLEANUP_SECONDS = 0.75
CHILDREN = Path('/proc/thread-self/children')


def enable_supervision():
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    # Check the cleanup primitives before allowing the command to start.
    CHILDREN.read_bytes()
    descriptor = os.pidfd_open(os.getpid())
    try:
        signal.pidfd_send_signal(descriptor, 0)
        try:
            os.waitid(os.P_PIDFD, descriptor, PEEK_CHILD)
        except ChildProcessError:
            pass  # This process is not its own child.
    finally:
        os.close(descriptor)


def reap(command):
    """Only ECHILD from this single-command subreaper certifies an empty tree."""
    for _ in range(64):  # Return to the cleanup clock even with a busy child tree.
        try:
            pid, status = os.waitpid(-1, os.WNOHANG | WAIT_ALL)
        except ChildProcessError:
            return True
        if pid == 0:
            return False
        if pid == command.pid:
            command.returncode = os.waitstatus_to_exitcode(status)
    return False


def stop_children(command):
    deadline = time.monotonic() + CLEANUP_SECONDS
    while time.monotonic() < deadline:
        try:
            if reap(command):
                return True
            children = CHILDREN.read_bytes().split()
        except OSError:
            children = []  # Retry transient procfs/wait failures within the same deadline.
        # Repeat as each generation becomes our direct children. This list
        # can omit concurrently exiting tasks. An empty read is never proof.
        for child in children:
            if time.monotonic() >= deadline:
                return False
            try:
                descriptor = os.pidfd_open(int(child))
                try:
                    # Check ownership through the very descriptor used to
                    # signal. WNOWAIT leaves child status for reap().
                    os.waitid(os.P_PIDFD, descriptor, PEEK_CHILD)
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                finally:
                    os.close(descriptor)
            except OSError:
                pass  # Other children can still be stopped. Retry until ECHILD or timeout.
        time.sleep(.005)
    return False


def supervise(owner, argv):
    try:
        enable_supervision()
        command = subprocess.Popen(argv, stdin=subprocess.DEVNULL, close_fds=True)
    except (OSError, ValueError, AttributeError) as exc:
        return {'launch_error': str(exc)[:256], 'error_type': type(exc).__name__}

    # The parent requests cleanup by shutting down its write half. Losing
    # the outer guest closes the same socket, even when it receives SIGKILL.
    try:
        while not reap(command) and command.returncode is None:
            readable, _, _ = select.select([owner], [], [], .01)
            if readable:
                break
    finally:
        stopped = stop_children(command)
    return {'exit_code': command.returncode, 'descendants_stopped': stopped}


if __name__ == '__main__':
    with socket.socket(fileno=int(sys.argv[1])) as channel:
        result = supervise(channel, sys.argv[2:])
        try:
            channel.sendall(json.dumps(result).encode('ascii'))
        except (BrokenPipeError, ConnectionResetError):
            pass  # The owner died. Cleanup ran before reporting its result.
