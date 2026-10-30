import sys
import hashlib
from starlette.testclient import TestClient
from workspace_runtime.runner import Runner
from workspace_runtime.service import create_app


def test_authenticated_dispatch_and_profile_boundary(tmp_path):
    (tmp_path/'backend.key').write_text('fixture-key')
    (tmp_path/'caller.sha256').write_text(hashlib.sha256(b'caller-key').hexdigest())
    runner=Runner(tmp_path,[sys.executable,'-c','import json; print(json.dumps({"text":"ready","memory_status":"ready"}))'])
    with TestClient(create_app('review',tmp_path,runner)) as client:
        body={'jsonrpc':'2.0','id':1,'method':'message/send','params':{'message':{
            'messageId':'message-1','role':'user','parts':[{'kind':'text','text':'check'}]}}}
        url='/profiles/review'
        assert client.post(url,json=body).status_code == 401
        auth={'Authorization':'Bearer fixture-key'}
        assert client.post(url,headers=auth,json=body).status_code == 403
        auth['X-Workspace-Key']='caller-key'
        response=client.post(url,headers=auth,json=body)
        assert response.status_code == 200
        assert response.json()['result']['status']['state'] == 'completed'
        assert client.post('/profiles/development',headers=auth,json=body).status_code == 404
        assert client.post(url,headers=auth,content='x'*70000).status_code == 413
        bad=body | {'method':'message/stream'}
        assert client.post(url,headers=auth,json=bad).json()['error']['code'] == -32601
        body['params']['message']['parts']=[{'kind':'file','file':{}}]
        assert client.post(url,headers=auth,json=body).json()['error']['code'] == -32602
