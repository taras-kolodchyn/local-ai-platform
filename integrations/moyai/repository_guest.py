#!/usr/bin/env python3
"""Fixed, nonroot, offline repository operations. Only controller SSH invokes this.

This helper is not a hostile-code or multi-tenant security boundary: conversations
share uid 10001. The controller owns the authoritative ledger and rechecks trees.
"""
import base64
from contextlib import contextmanager
import fcntl
import ctypes
import struct
import binascii
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import uuid
import tempfile
import time

MAX_INPUT = 16 * 1024 * 1024
MAX_OUTPUT = 64 * 1024
MAX_FILE = 512 * 1024
MAX_FILES = 512
MAX_TREE_BYTES = 8 * 1024 * 1024
MAX_DIAGNOSTICS = 8
MAX_DIAGNOSTIC_BYTES = 12 * 1024
MAX_TAIL = 4 * 1024
ID = re.compile(r'[A-Za-z0-9_-]{1,96}\Z')
CONVERSATION = re.compile(r'[a-f0-9]{64}\Z')
TRUSTED_CWD = Path('/opt/local-repository')
PROTECTED_TESTS = ('legacy_entry_point_retains_shared_policy_compatibility', 'shared_policy_caps_without_overflow', 'shared_policy_is_monotonic_and_bounded', 'shared_policy_starts_at_one_hundred_ms')
TERMINAL = {'passed', 'failed', 'timeout', 'cancelled', 'unknown'}


def bounded_text(value, limit):
    """Bound UTF-8 bytes even after replacement of malformed Unicode."""
    encoded = value.encode('utf-8', errors='replace')
    return encoded[:limit].decode('utf-8', errors='ignore'), len(encoded) > limit


class CargoMessages:
    """Bounded streaming metadata and untrusted diagnostics, never test evidence."""

    def __init__(self):
        self.buffer = bytearray()
        self.discard_line = False
        self.artifacts = []
        self.diagnostics = []
        self.diagnostic_bytes = 0
        self.truncated = False

    def feed(self, chunk):
        # Discard an oversized physical line through its newline. Never parse
        # a suffix as an independent Cargo record after resetting the buffer.
        parts = chunk.split(b'\n')
        for index, part in enumerate(parts):
            if not self.discard_line:
                if len(self.buffer) + len(part) > MAX_FILE:
                    self.buffer.clear()
                    self.discard_line = True
                    self.truncated = True
                else:
                    self.buffer.extend(part)
            if index < len(parts) - 1:
                if not self.discard_line:
                    self.line(bytes(self.buffer))
                self.buffer.clear()
                self.discard_line = False

    def finish(self):
        if self.buffer and not self.discard_line:
            self.line(bytes(self.buffer))
        self.buffer.clear()

    def line(self, line):
        # Cargo progress and ordinary stderr are retained by the prefix/tail;
        # their presence does not mean a structured diagnostic was truncated.
        if not line.lstrip().startswith((b'{', b'[')):
            return
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                return
            if (message.get('reason') == 'compiler-artifact' and message.get('executable')
                    and message.get('profile', {}).get('test') is True and len(self.artifacts) < 32):
                self.artifacts.append({'executable': message['executable'], 'target': message['target']})
            if message.get('reason') != 'compiler-message':
                return
            detail = message.get('message')
            if not isinstance(detail, dict) or detail.get('level') not in ('error', 'failure-note'):
                return
            code = detail.get('code')
            fields = {'level': detail['level'], 'code': code.get('code', '') if isinstance(code, dict) else '',
                      'message': detail.get('message', ''), 'rendered': detail.get('rendered') or ''}
            if not all(isinstance(value, str) for value in fields.values()):
                self.truncated = True
                return
            diagnostic = {}
            clipped = False
            for name, limit in (('level', 32), ('code', 64), ('message', 768), ('rendered', 2304)):
                diagnostic[name], cut = bounded_text(fields[name], limit)
                clipped |= cut
            size = sum(len(value.encode()) for value in diagnostic.values())
            if len(self.diagnostics) >= MAX_DIAGNOSTICS or self.diagnostic_bytes + size > MAX_DIAGNOSTIC_BYTES:
                self.truncated = True
                return
            diagnostic['truncated'] = clipped
            self.truncated |= clipped
            self.diagnostics.append(diagnostic)
            self.diagnostic_bytes += size
        except (ValueError, TypeError, AttributeError, KeyError, UnicodeError, RecursionError):
            # Malformed/unreadable lines are never proof. The raw prefix/tail
            # still exposes process errors that are not Cargo JSON.
            self.truncated = True


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON field')
        result[key] = value
    return result


def read_request(stream):
    raw = stream.read(MAX_INPUT + 1)
    if len(raw.encode('utf-8')) > MAX_INPUT:
        raise ValueError('request too large')
    try:
        value = json.loads(raw, object_pairs_hook=_unique)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise ValueError('malformed JSON') from exc
    if not isinstance(value, dict):
        raise ValueError('request must be an object')
    return value


def encode_response(result):
    wire = json.dumps(result, separators=(',', ':'), ensure_ascii=True)
    if len(wire.encode()) > MAX_INPUT:
        return '{"ok":false,"state":"failed","error":"response too large"}'
    return wire


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def sign_receipt(receipt, key):
    if key is None:
        return receipt
    body = {k: value for k, value in receipt.items() if k != 'receipt_mac'}
    wire = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return body | {'receipt_mac': hmac.new(bytes.fromhex(key), wire, hashlib.sha256).hexdigest()}


def safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise ValueError('unsafe path')
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in ('.', '..') or part.startswith('.') for part in path.parts):
        raise ValueError('unsafe path')
    if any(part in {'target', 'build.rs', 'rust-toolchain', 'rust-toolchain.toml', 'vendor'} for part in path.parts):
        raise ValueError('protected build path')
    if len(path.parts) > 12 or len(value) > 240:
        raise ValueError('path too long')
    return path


def editable(path):
    parts = PurePosixPath(path).parts
    return path in {'src/lib.rs', 'README.md'} or (len(parts) == 2 and parts[0] == 'tests' and parts[1].endswith('.rs'))


def ensure_directory(path):
    """Never follow a symlink in any existing path component."""
    for component in [*reversed(path.parents), path]:
        if component.exists() or component.is_symlink():
            if component.is_symlink() or not component.is_dir():
                raise ValueError('unsafe directory component')
        else:
            component.mkdir(mode=0o700)


def open_directory(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('absolute internal directory required')
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_fd(descriptor, limit, allow_links=False):
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or (not allow_links and before.st_nlink != 1) or before.st_size > limit:
        raise ValueError('nonregular, linked or oversized file')
    data = bytearray()
    while len(data) <= limit:
        chunk = os.read(descriptor, min(65536, limit + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
    after = os.fstat(descriptor)
    if len(data) > limit or len(data) != before.st_size or (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size):
        raise ValueError('file changed during bounded read')
    return bytes(data)


def read_regular(path, limit=MAX_FILE, allow_links=False):
    parent = open_directory(Path(path).parent)
    try:
        descriptor = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            return read_fd(descriptor, limit, allow_links=allow_links)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def source_bytes(root):
    output = {}
    total = 0
    root_fd = open_directory(root)
    def visit(directory, prefix=''):
        nonlocal total
        for name in sorted(os.listdir(directory)):
            relative = prefix + name
            safe_relative(relative)
            try:
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            except OSError as exc:
                raise ValueError('unsafe source entry') from exc
            try:
                info = os.fstat(descriptor)
                if stat.S_ISDIR(info.st_mode):
                    visit(descriptor, relative + '/')
                else:
                    data = read_fd(descriptor, MAX_FILE)
                    data.decode('utf-8')
                    if b'\x00' in data:
                        raise ValueError('binary project file')
                    total += len(data)
                    if len(output) >= MAX_FILES or total > MAX_TREE_BYTES:
                        raise ValueError('project too large')
                    output[relative] = data
            finally:
                os.close(descriptor)
    try:
        visit(root_fd)
    finally:
        os.close(root_fd)
    return output


def load_json(path):
    return json.loads(read_regular(path, MAX_INPUT), object_pairs_hook=_unique)


def save_json(path, value):
    ensure_directory(path.parent)
    parent = open_directory(path.parent)
    temporary = '.receipt-' + uuid.uuid4().hex
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, 'w') as output:
            json.dump(value, output, separators=(',', ':'))
            output.flush()
            os.fsync(output.fileno())
        os.rename(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


@contextmanager
def operation_lock(path):
    ensure_directory(path.parent)
    parent = open_directory(path.parent)
    try:
        for attempt in range(3):
            try:
                descriptor = os.open(path.name + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent)
                break
            except FileNotFoundError:
                if attempt==2:
                    raise
    finally:
        os.close(parent)
    try:
        info=os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            raise ValueError('unsafe operation lock')
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def incarnation(pid):
    """Bind a PID to a kernel boot, PID namespace/run, and process start tick."""
    try:
        def fields(target):
            return Path('/proc/' + str(target) + '/stat').read_text().rsplit(') ', 1)[1].split()
        values = fields(pid)
        return {'pid': int(pid), 'start_ticks': int(values[19]), 'process_group': int(values[2]),
                'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'pid_namespace': os.stat('/proc/self/ns/pid').st_ino, 'init_start_ticks': int(fields(1)[19])}
    except (OSError, ValueError, IndexError):
        # Host-only tests use the macOS kernel's proc_pidinfo start identity.
        if sys.platform != 'darwin':
            return None
        buffer=ctypes.create_string_buffer(136)
        library=ctypes.CDLL('/usr/lib/libproc.dylib')
        if library.proc_pidinfo(int(pid),3,0,buffer,136)!=136:
            return None
        seconds,micros=struct.unpack_from('QQ',buffer,120)
        group=struct.unpack_from('I',buffer,100)[0]
        return {'pid':int(pid),'start_seconds':seconds,'start_microseconds':micros,'process_group':group}



def same_incarnation(identity):
    return isinstance(identity, dict) and type(identity.get('pid')) is int and incarnation(identity['pid']) == identity


def verified_run_turnover(previous, current):
    for identity in (previous,current):
        if not isinstance(identity,dict):
            return False
        try:
            if str(uuid.UUID(identity.get('boot_id',''))) != identity['boot_id']:
                return False
        except (ValueError,TypeError,KeyError,AttributeError):
            return False
        if any(type(identity.get(key)) is not int or identity[key]<=0 for key in ('pid_namespace','init_start_ticks')):
            return False
    return any(previous[key]!=current[key] for key in ('boot_id','pid_namespace','init_start_ticks'))


def completed_tests(output, names, passed):
    expected = sorted(names)
    records = re.findall(r'^test ([^\n]+?) \.\.\. ([^\n]+)$', output, re.MULTILINE)
    if len(records) != len(expected) or sorted(name for name, _ in records) != expected:
        return False
    wanted = 'ok' if passed else 'FAILED'
    if any(state != wanted for _, state in records):
        return False
    summaries = re.findall(r'^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;', output, re.MULTILINE)
    if len(summaries) != 1:
        return False
    state, successes, failures, ignored, measured, filtered = summaries[0]
    return (state == ('ok' if passed else 'FAILED') and int(successes) == (len(expected) if passed else 0)
            and int(failures) == (0 if passed else len(expected)) and int(ignored) == 0 and int(measured) == 0)


def mandatory_commands_passed(commands):
    required={'oracle_build','oracle','baseline_build','baseline_probe','project_build',
              'counterexample_build','regression','regression_counterexample','project'}
    if not required <= {row.get('kind') for row in commands}:
        return False
    native={'oracle','baseline_probe','regression','regression_counterexample','interleaving_test','project'}
    for row in commands:
        expected=row.get('expected_exit_code') if row.get('kind') in ('regression_counterexample','interleaving_test') else 0
        if type(expected) is not int or expected not in (0,101):
            return False
        if row.get('exit_code')!=expected or row.get('state')!=('failed' if expected else 'passed'):
            return False
        if row.get('kind') in native and (row.get('native_completed') is not True or row.get('output_truncated') is not False):
            return False
    return any(row.get('kind')=='regression_counterexample' and row.get('exit_code')==101 for row in commands)


def regression_proof(path, identity, repaired, compiled, counter):
    valid = (compiled and counter is not None and counter.get('native_completed') is True
             and type(counter.get('exit_code')) is int and counter['exit_code'] in (0,101))
    passed = repaired is not None and repaired.get('native_completed') is True
    classification = ('detecting' if counter['exit_code']==101 else 'supporting') if valid and passed else 'invalid'
    if not passed:
        reason = 'Candidate test did not complete successfully.'
    elif not compiled:
        reason = 'Same-API counterexample did not compile; compilation failure is not detection.'
    elif not valid:
        reason = 'Counterexample test did not complete exactly; ignored, missing or truncated output is not detection.'
    elif classification=='supporting':
        reason = 'Passes both implementations; supports the suite but does not detect the 60,000 ms defect.'
    else:
        reason = 'Passes candidate and fails the compiled same-API 60,000 ms counterexample.'
    return {'path':path,'test_name':identity,'classification':classification,'reason':reason,
            'repaired_passed':passed,'counterexample_compiled':compiled,
            'counterexample_exit_code':counter.get('exit_code') if counter is not None else None,
            'counterexample_completed':bool(valid),'counterexample_failed':bool(valid and counter['exit_code']==101)}


def regression_proofs_passed(proofs):
    return (bool(proofs) and all(proof['classification'] in {'detecting','supporting'} for proof in proofs)
            and any(proof['classification']=='detecting' for proof in proofs))


def matches_files(root, expected):
    try:
        return {name: hashlib.sha256(data).hexdigest() for name, data in source_bytes(root).items()} == expected
    except (OSError, ValueError, UnicodeError):
        return False


def test_names(source):
    # Narrow Rust profile: ordinary test functions and inline named modules.
    # Remove comments/string contents before reading braces and attributes.
    cleaned=re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"', lambda match:' '*len(match.group()),source,flags=re.DOTALL)
    pattern=re.compile(r'mod\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{|#\s*\[\s*test\s*\]\s*(?:#\[[^\]]+\]\s*)*(?:pub\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(|[{}]')
    stack=[]
    names=set()
    for match in pattern.finditer(cleaned):
        module,function=match.groups()
        if module:
            stack.append(module)
        elif function:
            names.add('::'.join([part for part in stack if part]+[function]))
        elif match.group()=='{':
            stack.append(None)
        elif stack:
            stack.pop()
    return names


def function_span(source, name):
    cleaned=re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"',lambda match:' '*len(match.group()),source,flags=re.DOTALL)
    matches=list(re.finditer(r'pub\s+(?:(?:const|async|unsafe)\s+|extern\s+)*fn\s+'+re.escape(name)+r'\s*\(\s*(?:mut\s+)?(?P<argument>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*u32\s*,?\s*\)\s*->\s*u64\s*\{',cleaned))
    if len(matches)!=1:
        raise ValueError('counterexample requires one ordinary public retry function')
    start=matches[0].start()
    opening=matches[0].end()-1
    depth=1
    for position in range(opening+1,len(cleaned)):
        if cleaned[position]=='{': depth+=1
        elif cleaned[position]=='}':
            depth-=1
            if depth==0: return start,opening,position+1,matches[0].group('argument')
    raise ValueError('unbalanced retry function')


def counterexample_source(candidate, operator_template):
    # Preserve every unrelated candidate edit. Only the two retry-policy
    # functions become the operator-owned original 60,000 ms behavior.
    for name in ('bounded_backoff_ms','legacy_retry_delay_ms'):
        _,opening,end,argument=function_span(candidate,name)
        _,template_opening,template_end,_=function_span(operator_template,name)
        body=operator_template[template_opening+1:template_end-1]
        body=re.sub(r'\battempt\b',argument,body)
        candidate=candidate[:opening+1]+body+candidate[end-1:]
    return candidate


def interleaving_source(candidate, trigger):
    # Preserve the real bounded calculation and introduce state leakage in just one direction.
    names = ('bounded_backoff_ms','legacy_retry_delay_ms')
    if trigger not in names:
        raise ValueError('unknown retry entry point')
    _, opening, end, arg = function_span(candidate, names[0])
    pure = candidate[opening:end]
    for name in names:
        _, opening, end, argument = function_span(candidate, name)
        if name == trigger:
            prefix = f'if {argument} == u32::MAX {{ __MOYAI_EXTREME.set(true); }}'
        else:
            prefix = f'if {argument} <= 2 && __MOYAI_EXTREME.get() {{ return 30_000; }}'
        candidate = candidate[:opening] + '{' + prefix + f' __moyai_pure_delay({argument})' + '}' + candidate[end:]
    return (candidate + f'\nfn __moyai_pure_delay({arg}: u32) -> u64 {pure}\n'
            'std::thread_local! { static __MOYAI_EXTREME: std::cell::Cell<bool> = const {std::cell::Cell::new(false)}; }\n')


def passed_test_names(output):
    return {name for name in re.findall(r'^test ([A-Za-z_][A-Za-z0-9_:]*) \.\.\. ok$', output, re.MULTILINE)}


def test_count(output):
    return sum(int(n) for n in re.findall(r'test result: ok\. (\d+) passed;', output))


def command_stopped(result):
    return isinstance(result, dict) and all(
        result.get(key) is True for key in ('process_group_stopped', 'descendants_stopped', 'pipe_drained'))


class CommandScope:
    """The production guest delegates lifetime ownership to a distinct Linux process."""

    def __init__(self, argv, cwd, env):
        self.argv = argv
        self.channel = None
        self.launched = None
        self.exit_code = None
        self.launch_error = None
        self.descendants_stopped = False
        self.group_stopped = False
        options = dict(cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       stdin=subprocess.DEVNULL, start_new_session=True)
        if sys.platform == 'linux':
            channel, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            try:
                supervisor = Path(__file__).resolve().with_name('command_supervisor.py')
                self.process = subprocess.Popen([sys.executable, '-I', supervisor, str(peer.fileno()), *argv],
                                                pass_fds=(peer.fileno(),), **options)
                self.channel = channel
            except BaseException:
                channel.close()
                raise
            finally:
                peer.close()
        else:
            # Host-side output/diagnostic tests use macOS. Process groups
            # there provide no equivalent descendant-absence attestation.
            self.process = subprocess.Popen(argv, **options)
            self.launched = True

    def stop(self, identity):
        if self.channel is not None:
            try:
                self.channel.shutdown(socket.SHUT_WR)
            except OSError:
                pass  # finish() still requires a complete supervisor report.
        elif same_incarnation(identity):
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def finish(self, identity):
        self.stop(identity)
        try:
            status = self.process.wait(timeout=1)
            if self.channel is None:
                self.exit_code = status
                deadline = time.monotonic() + .3
                while Guest._alive(self.process.pid) and time.monotonic() < deadline:
                    time.sleep(.01)
                self.group_stopped = not Guest._alive(self.process.pid)
                return
            if status != 0:
                return
            self.channel.setblocking(False)
            report = json.loads(self.channel.recv(4096))
            if isinstance(report, dict) and isinstance(report.get('launch_error'), str):
                self.launch_error = {'argv': self.argv, 'error': report['launch_error'], 'error_type': report['error_type']}
                self.launched = False
                self.group_stopped = True
                return
            if isinstance(report, dict) and type(report.get('exit_code')) is int:
                self.launched = True
                self.exit_code = report['exit_code']
                self.descendants_stopped = report.get('descendants_stopped') is True
                self.group_stopped = self.descendants_stopped
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            # An inconclusive launch or cleanup keeps the operation fenced.
            pass
        finally:
            if self.channel is not None:
                self.channel.close()
            self.process.stdout.close()


class Guest:
    def __init__(self, workspace=Path('/workspace'), oracles=Path('/opt/local-repository/oracles')):
        self.workspace = Path(workspace)
        self.oracles = Path(oracles)

    def directory(self, conversation, create=True):
        if not isinstance(conversation, str) or not CONVERSATION.fullmatch(conversation):
            raise ValueError('invalid conversation identity')
        directory = self.workspace / conversation
        if create:
            ensure_directory(directory)
        return directory

    def operation_path(self, conversation, operation, create=True):
        if not isinstance(operation, str) or not ID.fullmatch(operation):
            raise ValueError('invalid operation identity')
        directory = self.directory(conversation, create=create) / '.repository-state' / 'operations'
        if create:
            ensure_directory(directory)
        return directory / (operation + '.json')

    def handle(self, request):
        if not isinstance(request, dict):
            raise ValueError('request must be an object')
        op = request.get('op')
        fields = {'import': {'manifest', 'files'}, 'tree': set(),
                  'check': {'task_id', 'operation_id', 'profile', 'timeout_seconds'},
                  'cancel': {'operation_id'}, 'status': {'operation_id'}}
        if not isinstance(op, str) or op not in fields:
            raise ValueError('unknown operation or fields')
        supplied = set(request) - ({'receipt_key','retry_contract'} if op == 'check' else {'receipt_key', 'task_id', 'tree_hash', 'profile'} if op == 'cancel' else set())
        if supplied != {'op', 'conversation_id'} | fields[op]:
            raise ValueError('unknown operation or fields')
        conversation = request['conversation_id']
        directory = self.directory(conversation, create=op not in {'status', 'cancel'})
        if op == 'import':
            return self.import_project(directory, request['manifest'], request['files'])
        if op == 'tree':
            return self.tree(directory)
        state_path = self.operation_path(conversation, request['operation_id'], create=op != 'status')
        if op in {'cancel', 'status'}:
            return self.operation(state_path, op == 'cancel', request)
        if not isinstance(request['task_id'], str) or not ID.fullmatch(request['task_id']):
            raise ValueError('invalid task identity')
        key = request.get('receipt_key')
        if key is not None and (not isinstance(key, str) or not CONVERSATION.fullmatch(key)):
            raise ValueError('invalid receipt key')
        if request['profile'] not in ('rust-retry', 'rust-retry-tests') or type(request['timeout_seconds']) not in (int, float) or not 1 <= request['timeout_seconds'] <= 300:
            raise ValueError('invalid fixed profile or timeout')
        if 'retry_contract' in request and (request['retry_contract'] != 'interleaved-extremes'
                or request['profile'] != 'rust-retry-tests'):
            raise ValueError('unknown retry acceptance contract')
        if state_path.exists():
            return self.operation(state_path)
        manifest = load_json(directory / '.repository-state' / 'manifest.json')
        if request['profile'] != manifest['verification_profile']:
            raise ValueError('check profile differs from pinned manifest')
        return self.check(directory, request, state_path)

    def import_project(self, directory, manifest, files):
        if not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int or manifest.get('schema_version') != 1 or manifest.get('verification_profile') not in ('rust-retry', 'rust-retry-tests'):
            raise ValueError('unsupported manifest')
        if not isinstance(manifest.get('snapshot_id'), str) or digest({k: v for k, v in manifest.items() if k != 'snapshot_id'}) != manifest['snapshot_id']:
            raise ValueError('snapshot digest mismatch')
        rows = manifest.get('files')
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES or not isinstance(files, list) or len(files) != len(rows):
            raise ValueError('invalid file list')
        expected = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'path', 'sha256', 'size'}:
                raise ValueError('invalid file manifest')
            name = safe_relative(row['path']).as_posix()
            if name in expected or type(row['size']) is not int or not 0 <= row['size'] <= MAX_FILE:
                raise ValueError('invalid file manifest')
            expected[name] = row
        decoded = {}
        for item in files:
            if not isinstance(item, dict) or set(item) != {'path', 'content_base64'} or not isinstance(item['path'], str) or item['path'] not in expected or item['path'] in decoded:
                raise ValueError('unexpected imported file')
            try:
                data = base64.b64decode(item['content_base64'], validate=True)
            except (binascii.Error, TypeError, ValueError) as exc:
                raise ValueError('invalid file encoding') from exc
            row = expected[item['path']]
            if len(data) != row['size'] or hashlib.sha256(data).hexdigest() != row['sha256'] or b'\x00' in data:
                raise ValueError('file hash or size mismatch')
            data.decode('utf-8')
            decoded[item['path']] = data
        if sum(map(len, decoded.values())) > MAX_TREE_BYTES or not {'Cargo.toml', 'Cargo.lock', 'src/lib.rs'} <= decoded.keys():
            raise ValueError('unsupported project size or files')
        # Cargo metadata is anchored to root-owned image data, never the import.
        for name in ('Cargo.toml', 'Cargo.lock'):
            if decoded[name] != read_regular(self.oracles / name):
                raise ValueError('unsupported package or dependency manifest')
        destination = directory / 'repository'
        if destination.exists() or destination.is_symlink():
            raise ValueError('repository already exists; refusing overwrite')
        staging = Path(tempfile.mkdtemp(prefix='.repository-import-', dir=directory))
        try:
            for name, data in decoded.items():
                output = staging / name
                ensure_directory(output.parent)
                output.write_bytes(data)
            state = directory / '.repository-state'
            ensure_directory(state)
            save_json(state / 'manifest.json', manifest)
            save_json(state / 'baseline-tests.json', {name: sorted(test_names(data.decode())) for name, data in decoded.items() if name.startswith('tests/') and name.endswith('.rs')})
            os.rename(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return self.tree(directory)

    def tree(self, directory):
        project = directory / 'repository'
        if project.is_symlink() or not project.is_dir():
            raise ValueError('repository missing or unsafe')
        manifest = load_json(directory / '.repository-state' / 'manifest.json')
        original = {row['path']: row for row in manifest['files']}
        files = []
        for relative, data in source_bytes(project).items():
            files.append({'path': relative, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'text': data.decode('utf-8')})
        files.sort(key=lambda row: row['path'])
        rows = [{k: row[k] for k in ('path', 'sha256', 'size')} for row in files]
        current = {row['path']: row for row in rows}
        protected = all(current.get(name) == row for name, row in original.items() if not editable(name))
        protected = protected and all(name in original or editable(name) for name in current)
        if manifest['verification_profile'] == 'rust-retry-tests':
            protected = (protected and all(current.get(name) == row for name, row in original.items())
                         and all(re.fullmatch(r'tests/[A-Za-z0-9_-]+\.rs', name) for name in current.keys() - original.keys()))
        for name in ('Cargo.toml', 'Cargo.lock'):
            protected = protected and current.get(name, {}).get('sha256') == hashlib.sha256(read_regular(self.oracles/name)).hexdigest()
        return {'ok': True, 'files': files, 'tree_hash': digest(rows), 'protected_unchanged': protected,
                'snapshot_id': manifest['snapshot_id']}

    def command(self, project, target):
        argv = ['/opt/rust/bin/cargo', 'test', '--offline', '--locked', '--jobs', '1',
                '--manifest-path', str(project/'Cargo.toml'), '--target-dir', str(target),
                '--no-run', '--message-format=json', '--all-targets']
        env = {'PATH': '/opt/rust/bin:/usr/bin:/bin', 'HOME': '/home/agent', 'CARGO_HOME': '/opt/rust-cargo',
               'RUSTUP_HOME': '/opt/rustup', 'RUSTUP_TOOLCHAIN': '1.100.0', 'CARGO_NET_OFFLINE': 'true',
               'CARGO_BUILD_JOBS': '1', 'CARGO_INCREMENTAL': '0', 'RUST_BACKTRACE': '0',
               'CARGO_TERM_COLOR': 'never', 'TMPDIR': str(target.parent), 'LANG': 'C.UTF-8'}
        return argv, env

    @staticmethod
    def _alive(group):
        try:
            os.killpg(group, 0)
            return True
        except ProcessLookupError:
            return False

    @staticmethod
    def claim(path, receipt):
        with operation_lock(path):
            if path.exists() or path.is_symlink():
                return False
            save_json(path, receipt)
            return True

    def operation(self, path, cancel=False, request=None):
        request = request or {}
        if not path.exists():
            receipt = {'ok': False, 'operation_id': path.stem, 'state': 'unknown', 'commands': [], 'tracked':False}
            if not cancel:
                return receipt  # A read cannot race a future claim by writing a tombstone.
            receipt.update(cancel_requested=True,tracked=True)
            key = request.get('receipt_key')
            if key is not None:
                if not isinstance(key, str) or not CONVERSATION.fullmatch(key) or not ID.fullmatch(request.get('task_id', '')) or not CONVERSATION.fullmatch(request.get('tree_hash', '')):
                    raise ValueError('invalid cancel binding')
                profile = request.get('profile', 'rust-retry')
                if profile not in ('rust-retry', 'rust-retry-tests'):
                    raise ValueError('invalid cancel profile')
                receipt.update(state='cancelled', task_id=request['task_id'], tree_hash=request['tree_hash'],
                               profile=profile, no_process_started=True, processes_stopped=True)
                receipt = sign_receipt(receipt, key)
            if self.claim(path, receipt):
                return receipt
        receipt = load_json(path)
        if not isinstance(receipt, dict) or receipt.get('state') not in TERMINAL | {'running'} or receipt.get('operation_id', path.stem) != path.stem:
            raise ValueError('invalid operation record')
        if receipt['state'] in TERMINAL:
            return receipt
        owner_valid = same_incarnation(receipt.get('owner_identity'))
        process_valid = same_incarnation(receipt.get('process_identity'))
        if not owner_valid:
            return {'ok': False, 'state': 'unknown', 'operation_id': path.stem, 'commands': [], 'tracked':True}
        if cancel:
            with operation_lock(path):
                settled=load_json(path)
                if settled['state'] in TERMINAL:
                    return settled
                # Re-read the owner after lock acquisition. Child incarnations
                # may legitimately retire/change while the observer waits.
                if not same_incarnation(settled.get('owner_identity')):
                    return {'ok':False,'state':'unknown','operation_id':path.stem,'commands':[],'tracked':True}
                save_json(path.with_suffix('.cancel'), {'cancel_requested': True})
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                receipt = load_json(path)
                if receipt['state'] in TERMINAL:
                    return receipt
                if not same_incarnation(receipt.get('owner_identity')):
                    return {'ok':False,'state':'unknown','operation_id':path.stem,'commands':[],'tracked':True}
                time.sleep(.02)
        elif receipt.get('process_identity') is not None and not process_valid:
            return {'ok':False,'state':'unknown','operation_id':path.stem,'commands':[],'tracked':True}
        return receipt

    def run_process(self, argv, cwd, env, timeout, state_path, settle=True):
        started = time.monotonic()
        scope = None
        reader = selectors.DefaultSelector()
        output = bytearray()
        seen = 0
        cargo = CargoMessages() if '--message-format=json' in argv else None
        tail = bytearray()
        outcome = None
        launch_error = None
        cleanup_deadline = None
        eof = False
        identity = None
        state = load_json(state_path)
        try:
            # A durable claim remains conservative across a crash immediately
            # before/after spawn. Only terminal settlement attests no live work.
            state.update(processes_stopped=False,launch_pending=True)
            save_json(state_path,state)
            scope = CommandScope(argv, cwd, env)
            process = scope.process
            identity = incarnation(process.pid)
            if identity is None:
                raise ValueError('unable to establish child process incarnation')
            state.update(process_group=process.pid, pid=process.pid, process_identity=identity, processes_stopped=False,no_process_started=False,launch_pending=False)
            save_json(state_path, state)
            reader.register(process.stdout, selectors.EVENT_READ)
            while True:
                now = time.monotonic()
                if state_path.with_suffix('.cancel').exists():
                    outcome = 'cancelled'
                elif now-started >= timeout and outcome is None:
                    outcome = 'timeout'
                # Start a cleanup clock independent of descriptor EOF.
                direct_done = process.poll() is not None
                if outcome is not None or direct_done:
                    if cleanup_deadline is None:
                        cleanup_deadline = now + 1
                        scope.stop(identity)
                for key, _ in reader.select(.02):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        eof = True
                        reader.unregister(key.fileobj)
                    if cargo is not None:
                        cargo.feed(chunk)
                    seen += len(chunk)
                    output.extend(chunk[:max(0, MAX_OUTPUT-len(output))])
                    tail.extend(chunk)
                    del tail[:-MAX_TAIL]
                if direct_done and eof:
                    break
                if cleanup_deadline is not None and time.monotonic() >= cleanup_deadline:
                    outcome = 'unknown'
                    break
        except (OSError, ValueError) as exc:
            outcome = 'unknown' if scope is not None else 'failed'
            if scope is None:
                launch_error={'argv':argv,'error':str(exc)[:256],'error_type':type(exc).__name__}
            encoded = str(exc).encode()[:MAX_OUTPUT]
            output.extend(encoded[:max(0, MAX_OUTPUT-len(output))])
        finally:
            if scope is not None:
                scope.finish(identity)
                if scope.launched is False:
                    launch_error = scope.launch_error
                    outcome = 'failed'
                elif not scope.group_stopped or not eof:
                    outcome = 'unknown'
            reader.close()
        if state_path.with_suffix('.cancel').exists() and outcome != 'unknown':
            outcome = 'cancelled'
        if cargo is not None:
            cargo.finish()
        encoded_tail = tail.decode('utf-8', errors='replace').encode()
        tail_text = encoded_tail[-MAX_TAIL:].decode('utf-8', errors='ignore')
        tail_cut = len(encoded_tail) > MAX_TAIL
        encoded_output = output.decode('utf-8', errors='replace').encode()
        launched = scope.launched if scope else False
        exit_code = scope.exit_code if scope else None
        result = {'argv': argv, 'exit_code': exit_code,
                  'state': outcome or ('passed' if exit_code == 0 else 'failed'),
                  'output': encoded_output[:MAX_OUTPUT].decode(errors='ignore'),
                  'output_truncated': seen>MAX_OUTPUT or len(encoded_output)>MAX_OUTPUT,
                  'output_tail': tail_text, 'output_tail_truncated': seen>MAX_TAIL or tail_cut,
                  'diagnostics': cargo.diagnostics if cargo is not None else [],
                  'diagnostics_truncated': cargo.truncated if cargo is not None else False,
                  'elapsed_seconds': round(time.monotonic()-started,6),
                  'process_group': scope.process.pid if scope and launched is not False else None, 'process_identity': identity,
                  'process_group_stopped': scope.group_stopped if scope else True,
                  'descendants_stopped': scope.descendants_stopped if scope else False,
                  'pipe_drained': eof if scope else True, 'artifacts': cargo.artifacts if cargo is not None else [],
                  'launched': launched, 'launch_error': launch_error}
        state.update(result if settle else {'state':'running','last_command':result, 'process_identity':None, 'process_group':None,
                                            'launch_pending':False,'processes_stopped':launched is False or command_stopped(result)})
        save_json(state_path,state)
        return result

    @staticmethod
    def materialize(root, files):
        ensure_directory(root)
        for name, data in files.items():
            destination = root/name
            ensure_directory(destination.parent)
            descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)

    @staticmethod
    def artifact(result, target, name):
        found = [item for item in result.get('artifacts', []) if item['target'].get('name') == name and item['target'].get('kind') == ['test']]
        if result['state'] != 'passed' or len(found) != 1:
            return None
        executable = Path(found[0]['executable'])
        try:
            executable.relative_to(target)
            descriptor = os.open(executable, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
                    return None
            finally:
                os.close(descriptor)
        except (ValueError, OSError):
            return None
        return executable

    def recover_scratch(self, directory):
        builds = directory/'.repository-state/builds'
        if not builds.exists():
            return
        if builds.is_symlink():
            raise ValueError('unsafe scratch root')
        current = incarnation(os.getpid())
        for child in builds.iterdir():
            if child.is_symlink() or not child.is_dir() or not ID.fullmatch(child.name):
                raise ValueError('unsafe abandoned scratch directory')
            operation = directory/'.repository-state/operations'/(child.name+'.json')
            receipt = load_json(operation) if operation.exists() else {}
            old_owner = receipt.get('owner_identity')
            different_run = verified_run_turnover(old_owner,current)
            commands = receipt.get('commands')
            stopped = (receipt.get('no_process_started') is True and commands == []
                       or isinstance(commands, list) and bool(commands) and all(command_stopped(row) for row in commands))
            if (receipt.get('state') in {'passed','failed','timeout','cancelled'} and receipt.get('processes_stopped') is True and stopped) or different_run:
                shutil.rmtree(child)
            else:
                # Do not accumulate unconfirmed abandoned work across retries.
                raise ValueError('unreconciled scratch work requires cancellation or a fresh sandbox run')

    def check(self, directory, request, state_path):
        start = time.monotonic()
        before = self.tree(directory)
        source = {row['path']:row['text'].encode() for row in before['files']}
        baseline_tests = load_json(directory/'.repository-state/baseline-tests.json')
        changed_tests = {}
        for name, data in source.items():
            if name.startswith('tests/') and name.endswith('.rs'):
                added = test_names(data.decode()) - set(baseline_tests.get(name, []))
                if added:
                    stem = PurePosixPath(name).stem
                    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', stem):
                        changed_tests[name] = None
                    else:
                        changed_tests[name] = sorted(added)
        receipt = {'ok':False,'state':'running','operation_id':request['operation_id'],'task_id':request['task_id'],
                   'profile':request['profile'],'tree_hash':before['tree_hash'],'commands':[], 'checks':{},
                   'owner_identity':incarnation(os.getpid()), 'no_process_started':True, 'processes_stopped':False,'launch_errors':[]}
        invalid = None
        if not before['protected_unchanged']:
            invalid = 'protected project data changed'
        elif len(changed_tests)>8:
            invalid = 'at most eight changed regression targets are supported'
        elif any(names is None for names in changed_tests.values()):
            invalid = 'unsupported regression target name'
        if invalid:
            receipt.update(state='failed',error=invalid,processes_stopped=True,checks={'protected_unchanged':before['protected_unchanged']})
            receipt = sign_receipt(receipt,request.get('receipt_key'))
            return receipt if self.claim(state_path,receipt) else self.operation(state_path)
        if not self.claim(state_path,receipt):
            return self.operation(state_path)
        scratch = directory/'.repository-state/builds'/request['operation_id']
        commands = []
        proofs = []
        checks = {'protected_unchanged':True,'tree_unchanged':True,'verification_inputs_unchanged':True,
                  'baseline_policy_violation':False,'oracle_passed':False,'protected_test_names':[],
                  'regression_tests_added':sum(len(names) for names in changed_tests.values()),
                  'regression_tests_run':0,'regression_counterexample_passed':False,
                  'project_tests_run':0,'oracle_tests_run':0,'regression_proofs':[]}
        inputs = []
        executable_hashes = {}
        def cancelled():
            return state_path.with_suffix('.cancel').exists()
        def execute(argv, env, kind, cwd=TRUSTED_CWD):
            if cancelled():
                raise InterruptedError('cancelled before spawn')
            if not all(matches_files(root, hashes) for root, hashes in inputs):
                checks['verification_inputs_unchanged'] = False
                raise ValueError('verification input mutation')
            remaining = request['timeout_seconds']-(time.monotonic()-start)
            if remaining<=0:
                raise TimeoutError('check deadline reached')
            if argv[0] in executable_hashes and hashlib.sha256(read_regular(Path(argv[0]),128*1024*1024,allow_links=True)).hexdigest()!=executable_hashes[argv[0]]:
                raise ValueError('compiled test executable changed')
            # A tracking or final-write error after dispatch cannot prove that
            # nothing started, even when all earlier commands were stopped.
            receipt.update(no_process_started=False, processes_stopped=False)
            result = self.run_process(argv,cwd,env,remaining,state_path,settle=False)
            result['kind'] = kind
            if result['launched'] is None:
                receipt['state'] = 'unknown'
                raise InterruptedError('command supervisor did not confirm execution and cleanup')
            if result['launched'] is False:
                receipt['launch_errors'].append(result['launch_error'] | {'kind':kind})
                receipt['no_process_started'] = not commands
                receipt['processes_stopped'] = all(command_stopped(row) for row in commands)
                raise ValueError('command did not start')
            commands.append(result)
            receipt['processes_stopped'] = all(command_stopped(row) for row in commands)
            if not receipt['processes_stopped']:
                receipt['state'] = 'unknown'
                raise InterruptedError('command cleanup is unconfirmed')
            if result['state'] in {'timeout','cancelled','unknown'}:
                receipt['state'] = result['state']
                raise InterruptedError(result['state'])
            return result
        def build(project, target, kind, test=None):
            argv, env = self.command(project,target)
            if test:
                at = argv.index('--all-targets')
                argv[at:at+1] = ['--test',test]
            result=execute(argv,env,kind)
            for artifact in result.get('artifacts',[]):
                executable=Path(artifact['executable'])
                try: executable.relative_to(target)
                except ValueError: raise ValueError('compiled executable outside target')
                executable_hashes[str(executable)]=hashlib.sha256(read_regular(executable,128*1024*1024,allow_links=True)).hexdigest()
            return result,env
        def native(executable, env, names, kind, passed=True, test_path=None):
            argv=[str(executable),'--test-threads=1']
            if len(names)==1:
                argv += ['--exact',names[0]]
            result=execute(argv,env,kind)
            result['test_names']=names
            if test_path is not None:
                result['test_path']=test_path
            # Counterexample tests may detect the defect (101) or support the
            # suite (0). Both need exact native completion, never a compile exit.
            if passed is None:
                passed=result['exit_code']==0
            result['expected_exit_code']=0 if passed else 101
            result['native_completed']=completed_tests(result['output'],names,passed) and not result['output_truncated'] and result['exit_code']==(0 if passed else 101)
            return result
        try:
            # Recovery is bounded: one unresolved scratch tree fences new work.
            self.recover_scratch(directory)
            if cancelled():
                raise InterruptedError('cancelled before source materialization')
            copied=scratch/'source'
            self.materialize(copied,source)
            counter=scratch/'counterexample'
            counter_files=dict(source)
            if changed_tests:
                counter_files['src/lib.rs']=counterexample_source(source['src/lib.rs'].decode(),read_regular(self.oracles/'counterexample_lib.rs').decode()).encode()
            self.materialize(counter,counter_files)
            verifier=scratch/'verifier'
            probe=scratch/'probe'
            for root,package,oracle,lock in ((verifier,'repository-oracle','retry_policy','Verifier.lock'),(probe,'repository-baseline','legacy_policy','Baseline.lock')):
                manifest=('[package]\nname="'+package+'"\nversion="0.0.0"\nedition="2024"\n'
                          '[dependencies]\nlocal-ai-rust-service={path="../source"}\n[[test]]\nname="'+oracle+'"\npath="'+str(self.oracles/(oracle+'.rs'))+'"\n').encode()
                self.materialize(root,{'Cargo.toml':manifest,'Cargo.lock':read_regular(self.oracles/lock)})
            for root,files in ((copied,source),(counter,counter_files)):
                inputs.append((root,{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}))
            for root in (verifier,probe):
                inputs.append((root,{name:hashlib.sha256(read_regular(root/name)).hexdigest() for name in ('Cargo.toml','Cargo.lock')}))
            # Protected target trees are independent and execute before any project
            # or agent-authored regression executable can touch later build inputs.
            oracle_target=scratch/'targets/oracle'
            oracle_build,env=build(verifier,oracle_target,'oracle_build','retry_policy')
            oracle=self.artifact(oracle_build,oracle_target,'retry_policy')
            if oracle:
                result=native(oracle,env,list(PROTECTED_TESTS),'oracle')
                if result['native_completed']:
                    checks.update(oracle_passed=True,protected_test_names=list(PROTECTED_TESTS),oracle_tests_run=4)
            probe_target=scratch/'targets/baseline'
            probe_build,env=build(probe,probe_target,'baseline_build','legacy_policy')
            baseline=self.artifact(probe_build,probe_target,'legacy_policy')
            if baseline:
                name=['legacy_large_attempt_obeys_documented_ceiling']
                result=native(baseline,env,name,'baseline_probe')
                checks['baseline_policy_violation']=(result['exit_code']==101 and completed_tests(result['output'],name,False) and 'left: 60000' in result['output'] and 'right: 30000' in result['output'])
            # Candidate and counterexample builds use separate targets; a compile
            # failure in the counterexample is never evidence of a detecting test.
            project_target=scratch/'targets/project'
            project_build,project_env=build(copied,project_target,'project_build')
            regression_binaries=[]
            for name,names in changed_tests.items():
                stem=PurePosixPath(name).stem
                repaired=self.artifact(project_build,project_target,stem)
                counter_target=scratch/'targets'/('counter-'+stem)
                counter_build,counter_env=build(counter,counter_target,'counterexample_build',stem)
                counter_build['test_path']=name
                broken=self.artifact(counter_build,counter_target,stem)
                regression_binaries.append((name,names,repaired,broken,counter_env))
            for name,names,repaired,broken,counter_env in regression_binaries:
                for identity in names:
                    repaired_result=native(repaired,project_env,[identity],'regression',test_path=name) if repaired else None
                    counter_result=native(broken,counter_env,[identity],'regression_counterexample',passed=None,test_path=name) if broken else None
                    proofs.append(regression_proof(name,identity,repaired_result,broken is not None,counter_result))
            if changed_tests and request.get('retry_contract') == 'interleaved-extremes':
                detected = []
                for trigger in ('bounded_backoff_ms','legacy_retry_delay_ms'):
                    root, target = scratch/trigger, scratch/'targets'/trigger
                    files = {**source,'src/lib.rs':interleaving_source(source['src/lib.rs'].decode(), trigger).encode()}
                    self.materialize(root, files)
                    inputs.append((root,{p:hashlib.sha256(v).hexdigest() for p,v in files.items()}))
                    compiled, env = build(root, target, 'interleaving_build')
                    compiled['trigger'] = trigger
                    for path, identities in changed_tests.items():
                        binary = self.artifact(compiled, target, PurePosixPath(path).stem)
                        if binary:
                            for identity in identities:
                                run = native(binary, env, [identity], 'interleaving_test', passed=None, test_path=path)
                                run['trigger'] = trigger
                                if run['native_completed'] and run['exit_code'] == 101 and trigger not in detected:
                                    detected.append(trigger)
                checks['extreme_interleaving_detected'] = detected
            # Execute each actual project binary independently from Cargo metadata.
            if project_build['state']=='passed':
                for item in project_build.get('artifacts',[]):
                    if not item['target'].get('test') or not item.get('executable'):
                        continue
                    executable=Path(item['executable'])
                    try: executable.relative_to(project_target)
                    except ValueError: raise ValueError('project test executable escaped target')
                    listed=execute([str(executable),'--list','--format','terse'],project_env,'project_list')
                    names=re.findall(r'^([A-Za-z_][A-Za-z0-9_:]*): test$',listed['output'],re.MULTILINE)
                    if listed['state']!='passed' or listed['output_truncated']:
                        raise ValueError('unable to list project tests')
                    if names:
                        result=native(executable,project_env,names,'project')
                        if not result['native_completed']:
                            raise ValueError('project tests did not complete')
                        checks['project_tests_run']+=len(names)
            checks['regression_proofs']=proofs
            checks['regression_tests_run']=sum(proof['repaired_passed'] for proof in proofs)
            checks['regression_counterexample_passed']=regression_proofs_passed(proofs)
            if not checks['regression_counterexample_passed']:
                receipt['failure_code']='regression_requirement'
                receipt['error']=('Every added test must pass the candidate and complete on the compiled counterexample; at least one must fail its 60,000 ms policy. See regression_proofs for each identity.')
            receipt['state']='passed' if (checks['oracle_passed'] and checks['regression_counterexample_passed'] and checks['project_tests_run']>0 and mandatory_commands_passed(commands) and not receipt['launch_errors']) else 'failed'
            if changed_tests and request.get('retry_contract') and len(checks['extreme_interleaving_detected']) != 2:
                receipt.update(state='failed', failure_code='extreme_interleaving',
                    error='Exercise u32::MAX on each retry entry point and assert exact small delays on the other '
                          'in both call orders. The new tests must detect both cross-call state regressions.')
        except TimeoutError:
            receipt['state']='timeout'
        except InterruptedError:
            if receipt['state']=='running':
                receipt['state']='cancelled' if cancelled() else 'unknown'
        except (ValueError,OSError,UnicodeError) as exc:
            receipt.update(state='failed' if (receipt['no_process_started'] or receipt['processes_stopped']) else 'unknown',error=str(exc)[:256])
        finally:
            if receipt['no_process_started']:
                receipt['processes_stopped']=True
            try:
                after=self.tree(directory)
                checks['tree_unchanged']=before['tree_hash']==after['tree_hash']
                checks['protected_unchanged']=after['protected_unchanged']
                receipt['tree_hash']=after['tree_hash']
                checks['verification_inputs_unchanged']=checks['verification_inputs_unchanged'] and all(matches_files(root,hashes) for root,hashes in inputs)
            except (ValueError,OSError,UnicodeError):
                checks['tree_unchanged']=False
            if not all(checks[key] for key in ('tree_unchanged','protected_unchanged','verification_inputs_unchanged')) and receipt['state']=='passed':
                receipt['state']='failed'
            # Cancellation and publication serialize under the same operation lock.
            with operation_lock(state_path):
                if cancelled() and receipt['state']!='unknown':
                    receipt['state']='cancelled'
                if scratch.exists() and receipt['processes_stopped']:
                    shutil.rmtree(scratch)
                receipt.update(ok=receipt['state']=='passed',commands=commands,checks=checks,
                               elapsed_seconds=round(time.monotonic()-start,6),resource={'peak_rss_bytes':None})
                receipt=sign_receipt(receipt,request.get('receipt_key'))
                save_json(state_path,receipt)
        return receipt


def main():
    try:
        result = Guest().handle(read_request(sys.stdin))
    except (ValueError, OSError, UnicodeError) as exc:
        result = {'ok': False, 'state': 'failed', 'error': str(exc)[:256]}
    print(encode_response(result))


if __name__ == '__main__':
    main()
