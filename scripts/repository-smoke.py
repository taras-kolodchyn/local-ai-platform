#!/usr/bin/env python3
"""Run real local-model repository tasks and retain every acceptance outcome privately."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integrations/moyai'))
from client import MoyaiClient, NoRedirects
from repository_acceptance import require, validate_evidence

COMPOSE = ['docker', 'compose', '--project-name', 'local-ai-moyai', '--file', 'compose.moyai.yaml']


def compose(*arguments):
    return subprocess.check_output(COMPOSE + list(arguments), cwd=ROOT, text=True, timeout=240)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def file_rows(directory):
    return [{'path':path.relative_to(directory).as_posix(), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
             'size':path.stat().st_size} for path in sorted(directory.rglob('*')) if path.is_file()
            and 'target' not in path.relative_to(directory).parts]


class ResourceSampler:
    """Operator-side observation; Docker access never enters the agent workspace."""
    def __init__(self, path):
        self.path, self.stop = path, threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self):
        with self.path.open('a') as stream:
            while not self.stop.is_set():
                sample = {'timestamp':time.time()}
                try:
                    ids = subprocess.check_output(['docker','ps','--filter',
                        'label=com.docker.compose.project=local-ai-moyai','--format','{{.ID}}'],
                        text=True, timeout=10).split()
                    if ids:
                        values = subprocess.check_output(['docker','stats','--no-stream','--format','{{json .}}',*ids],
                                                         text=True, timeout=15)
                        sample['containers'] = [json.loads(line) for line in values.splitlines()]
                    processes = subprocess.check_output(['ps','-axo','pid=,rss=,comm='],text=True,timeout=10)
                    sample['native_inference'] = [line.strip() for line in processes.splitlines()
                        if any(name in line.casefold() for name in ('model-runner','llama-server','com.docker.model'))]
                except (OSError, subprocess.SubprocessError, ValueError) as error:
                    sample['observation_error'] = type(error).__name__
                stream.write(json.dumps(sample) + '\n'); stream.flush()
                self.stop.wait(5)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set(); self.thread.join(timeout=40)


def observer(session):
    return json.loads(compose('exec','-T','--user','10001:10001','moyai','python',
                              '/opt/moyai-local/tests/observe_repository.py',session))


def gateway_access_guards(binding):
    opener = build_opener(ProxyHandler({}), NoRedirects())
    search = '/v1/vector_stores/' + binding['store_id'] + '/search'
    cases = [
        ('context_search','context-gateway-key',search,{'query':'retry ceiling','max_num_results':2},200),
        ('context_foreign_store','context-gateway-key','/v1/vector_stores/vs_unregistered_probe/search',{'query':'retry ceiling'},403),
        ('context_inference','context-gateway-key','/v1/responses',{'model':'local-qwen','input':'Access guard'},403),
        ('context_management','context-gateway-key','/key/generate',{},403),
        ('inference_vector_search','litellm-key',search,{'query':'retry ceiling'},403),
        ('inference_mcp','litellm-key','/local_retrieval/mcp',{'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}},403),
    ]
    outcomes = {}
    for name,file,route,body,expected in cases:
        key = (ROOT/'.local/moyai'/file).read_text().strip()
        request = Request('http://127.0.0.1:4000'+route,data=json.dumps(body).encode(),
                          headers={'Authorization':'Bearer '+key,'Content-Type':'application/json',
                                   'Accept':'application/json, text/event-stream'})
        try:
            with opener.open(request,timeout=60) as response:
                code = response.status
                if expected == 200:
                    require(bool(json.load(response).get('data')), 'Approved context search returned no source')
        except HTTPError as error:
            code = error.code; error.close()
        require(code == expected, f'Gateway access boundary failed: {name}, HTTP {code}')
        outcomes[name] = code
    return outcomes


def validate_patch(patch, manifest, snapshot, observed):
    with tempfile.TemporaryDirectory(prefix='repository-patch-') as directory:
        checkout = Path(directory)
        for row in manifest['files']:
            output = checkout / row['path']; output.parent.mkdir(parents=True, exist_ok=True)
            data = (snapshot / 'source' / row['path']).read_bytes()
            require(hashlib.sha256(data).hexdigest() == row['sha256'], 'Immutable source changed')
            output.write_bytes(data)
        subprocess.run(['git','apply','--check','-'],input=patch,cwd=checkout,check=True,capture_output=True)
        subprocess.run(['git','apply','-'],input=patch,cwd=checkout,check=True,capture_output=True)
        require(file_rows(checkout) == observed['files'], 'Downloaded patch does not reproduce the verified source')


def run_one(client, binding, manifest, snapshot, directory, number):
    session = client.session(f'Repository acceptance {number}: bounded Rust retry')
    record = {'session':session,'started_at':datetime.now(timezone.utc).isoformat(),'status':'failed'}
    started = time.monotonic()
    try:
        prompt = f'''Complete the registered repository task for repository_id {binding['repository_id']}.
Use list_repositories, then open_repository with this ID. Work through all required phases.
Find the documented retry policy using search_repository and inspect the actual source files.
Run run_repository_checks before any edits to demonstrate the original failure.
Write a native plan with explicit acceptance criteria: retry delays start at 100 milliseconds,
increase exponentially, and never exceed 30,000 milliseconds, including extreme u32 attempts.
Then call begin_repository_changes. Implement the public shared bounded_backoff_ms(u32) -> u64
policy. Retain the public name and signature of legacy_retry_delay_ms, and give it the same
corrected policy: both entry points must return identical values for every attempt, including
30,000 at attempt 10 and u32::MAX. Add a focused integration regression under tests/ covering
both entry points that actually fails against the old 60,000-millisecond ceiling and passes the repair.
Only change the retry implementation and its regression tests. Keep package/dependency data,
existing tests and unrelated service behavior intact. Use the native file tools, relative project
paths, and the fixed run_repository_checks tool. Read actual validation output and repair any
failures within the task budget. Finish with finish_repository_task so the patch, test report
and result manifest are available through Files. Complete the work, then give a brief factual
report with source citations and download links. Do not ask questions or delegate.'''
        turn, detail = client.turn(session,prompt,timeout=1200)
        record['turn'] = turn
        task = client.request(f'/api/sessions/{session}/repository-task')
        record['task'] = task
        observed = observer(session)
        record['observation'] = observed
        progress = client.request(f'/api/sessions/{session}/progress/{turn}?include_activity=true')
        record['progress'] = progress
        completed = {tool['name'] for tool in progress.get('tools',[]) if tool.get('status') == 'completed'}
        require(task['phase'] == 'completed', f"Turn ended in repository phase {task['phase']}; see its native tool history")
        bodies = {}
        listed = {row['id']:row for row in client.request(f'/api/sessions/{session}/files')}
        for path in ('repository-results/changes.patch','repository-results/test-report.json','repository-results/result.json'):
            native = (task.get('files') or {}).get(path,{})
            require(native.get('id') in listed, 'Missing authenticated native Files entry')
            data = client.request('/api/files/' + native['id'],raw=True)
            digest = hashlib.sha256(data).hexdigest()
            require(digest == native.get('sha256') == task['artifacts'].get(path), 'Downloaded artifact hash differs')
            bodies[path.split('/')[-1]] = data
            (directory / f'{number}-{path.split("/")[-1]}').write_bytes(data)
        result = json.loads(bodies['result.json']); report = json.loads(bodies['test-report.json'])
        validate_evidence(task, observed, result, report, completed, binding,
                          {'repository-results/'+name:hashlib.sha256(bodies[name]).hexdigest()
                           for name in ('changes.patch','test-report.json')})
        for citation in task['citations']:
            name = citation['path']
            require(name in {row['path'] for row in manifest['files']}, 'Citation refers to foreign source')
            lines = (snapshot/'source'/name).read_text().splitlines()
            require(1 <= citation['start_line'] <= citation['end_line'] <= len(lines), 'Citation line range is invalid')
        replies = [m['content'] for m in detail['messages']
                   if m.get('turn_id') == turn and m.get('role') == 'assistant']
        policy_ranges = [f"{c['path']}:{c['start_line']}–{c['end_line']}" for c in task['citations']
                         if c['path'] == 'docs/architecture-rules.md' and c['start_line'] <= 7 <= c['end_line']]
        require(replies and any(ref in replies[-1] for ref in policy_ranges),
                'Final chat reply is missing the retrieved policy citation')
        validate_patch(bodies['changes.patch'],manifest,snapshot,observed)
        record['status'] = 'passed'
        record['artifact_hashes'] = {name:hashlib.sha256(data).hexdigest() for name,data in bodies.items()}
        return record, bodies
    except Exception:
        try:
            record['task'] = client.request(f'/api/sessions/{session}/repository-task')
            active = json.loads((ROOT/'.local/moyai/last-session.json').read_text())
            if active.get('session') == session:
                record['turn'] = active['turn']
                record['progress'] = client.request(f'/api/sessions/{session}/progress/{active["turn"]}?include_activity=true')
        except Exception:
            pass
        record['error'] = traceback.format_exc()
        raise
    finally:
        record['seconds'] = round(time.monotonic()-started,3)
        (directory/f'{number}-receipt.json').write_text(json.dumps(record,indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs',type=int,default=2,choices=range(1,4))
    parser.add_argument('--restart',action='store_true')
    args = parser.parse_args()
    os.chdir(ROOT); os.umask(0o077)
    (ROOT/'.local/verification').mkdir(parents=True,exist_ok=True,mode=0o700)
    directory = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),
                                     dir=ROOT/'.local/verification'))
    print(f'Repository acceptance: private evidence {directory}',flush=True)
    source = ROOT/'examples/rust-service'; before = file_rows(source)
    summary = {'status':'failed','requested_runs':args.runs,'runs':[],
               'restart_requested':args.restart,'model_alias':'local-qwen'}
    report_path = directory/'result.json'
    report_path.write_text(json.dumps(summary,indent=2)+'\n')
    try:
        registration = subprocess.run([sys.executable,'scripts/prepare-repository.py','--smoke-fixture'],
                                     check=True,stdout=subprocess.PIPE,text=True,timeout=1800)
        binding = json.loads(registration.stdout); summary['binding'] = binding
        snapshot = ROOT/'.local/repositories/snapshots'/binding['snapshot_id']
        manifest = json.loads((snapshot/'manifest.json').read_text())
        summary['fixture_git'] = manifest['git']
        client = MoyaiClient(); client.login()
        access = gateway_access_guards(binding)
        (directory/'gateway-access.json').write_text(json.dumps(access,indent=2)+'\n')
        guards = json.loads(compose('exec','-T','moyai','python',
                                   '/opt/moyai-local/tests/exercise_repository_runtime.py', json.dumps(binding)))
        (directory/'runtime-guards.json').write_text(json.dumps(guards,indent=2)+'\n')
        with ResourceSampler(directory/'resources.jsonl'):
            for number in range(1,args.runs+1):
                print(f'Repository acceptance: fresh local-model task {number}/{args.runs}',flush=True)
                record,bodies = run_one(client,binding,manifest,snapshot,directory,number)
                if args.restart and number == 1:
                    compose('restart','moyai','moyai-sandbox','moyai-temporal','moyai-bridge')
                    compose('up','--detach','--wait','--wait-timeout','180')
                    client = MoyaiClient(); client.login()
                    task = client.request(f'/api/sessions/{record["session"]}/repository-task')
                    require(task == record['task'], 'Persisted controller state changed across settled restart')
                    require(observer(record['session'])['tree_hash'] == task['verified_tree'], 'Workspace changed on restart')
                    for path,native in task['files'].items():
                        require(client.request('/api/files/'+native['id'],raw=True) == bodies[path.split('/')[-1]],
                                'Downloaded artifact changed on restart')
        print('Repository acceptance: real model edits, protected Rust checks, patch and Files verified',flush=True)
        summary['status'] = 'passed'
    except BaseException:
        summary['error'] = traceback.format_exc()
        raise
    finally:
        unchanged = file_rows(source) == before
        summary['host_source_unchanged'] = unchanged
        summary['runs'] = [{key:record.get(key) for key in ('session','turn','status','seconds')}
                           for path in sorted(directory.glob('*-receipt.json'))
                           for record in [json.loads(path.read_text())]]
        if not unchanged:
            summary['status'] = 'failed'
        report_path.write_text(json.dumps(summary,indent=2)+'\n')
        require(unchanged, 'The host fixture was modified')


if __name__ == '__main__':
    main()
