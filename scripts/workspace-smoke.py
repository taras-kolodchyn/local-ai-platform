#!/usr/bin/env python3
"""Exercise the real registered text execution endpoints."""
import importlib.util
import json
from pathlib import Path
import uuid

spec=importlib.util.spec_from_file_location('provision',Path(__file__).with_name('provision-workspace.py'))
p=importlib.util.module_from_spec(spec); spec.loader.exec_module(p)


def main():
    registry=json.loads((p.LOCAL/'workspace-registry.json').read_text())
    admin=p.gateway((p.LOCAL/'litellm-master-key').read_text().strip())
    routes=admin('GET','/v1/agents')
    if isinstance(routes,dict):
        routes=routes['agents']
    by_id={route['agent_id']:route for route in routes}
    for profile,sid in registry['routes'].items():
        assert any(key.get('key_alias')=='workspace-'+profile
                   for key in by_id[sid].get('keys') or []), 'Dashboard key association missing'
    catalog=admin('GET','/claude-code/plugins')['plugins']
    manifest=json.loads((p.ROOT/'execution/skills/manifest.json').read_text())
    by_name={skill['name']:skill for skill in catalog}
    for skill in manifest['skills']:
        assert by_name[skill['name']]['source']['sha']==manifest['commit'], 'Dashboard skill pin missing'
    print('Dashboard skill catalog and profile key associations passed')
    request=p.gateway((p.LOCAL/'workspace-client-key').read_text().strip(), execution=True)
    for profile,sid in registry['routes'].items():
        response=request('POST',f'/a2a/{sid}', {'jsonrpc':'2.0','id':1,'method':'message/send',
            'params':{'message':{'messageId':uuid.uuid4().hex,'role':'user',
                'parts':[{'kind':'text','text':'Reply with exactly WORKSPACE-READY. Do not call any tools.'}]}}})
        if 'error' in response:
            raise RuntimeError(f'{profile}: gateway protocol error {response["error"].get("code")}')
        result=response['result']
        assert result['status']['state']=='completed', f'{profile}: {result["status"]["state"]}'
        assert 'WORKSPACE-READY' in json.dumps(result['artifacts']), f'{profile}: unexpected result'
        print(profile, 'gateway execution passed')
    denied=p.gateway((p.LOCAL/'litellm-api-key').read_text().strip())
    denied_id=uuid.uuid4().hex
    try:
        result=denied('POST',f'/a2a/{sid}', {'jsonrpc':'2.0','id':2,'method':'message/send',
            'params':{'message':{'messageId':denied_id,'role':'user',
                'parts':[{'kind':'text','text':'Reply with WORKSPACE-READY.'}]}}})
        assert 'error' in result, 'Unscoped key executed a task'
        print('Execution backend denied unscoped request')
    except RuntimeError as exc:
        assert any(code in str(exc) for code in ('403', '404', '500')), str(exc)
    lookup=request('POST',f'/a2a/{sid}', {'jsonrpc':'2.0','id':3,'method':'tasks/get',
        'params':{'id':denied_id}})
    assert lookup.get('error', {}).get('code') == -32001, 'Denied invocation persisted a task'
    print('Execution gateway authorization passed')


if __name__=='__main__': main()
