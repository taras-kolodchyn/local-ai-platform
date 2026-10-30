import os

from starlette.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@127.0.0.1/unused")

from local_ai_retrieval import service


def test_mounted_transport_lists_and_calls_tools(monkeypatch):
    monkeypatch.setattr(service, "migrate", lambda _: None)
    monkeypatch.setattr(service, "db_get_chunk", lambda *_: {"id": 1, "content": "fixture"})
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    with TestClient(service.app, base_url="http://127.0.0.1:8000", headers=headers) as client:
        response = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert response.status_code == 200
        assert {tool["name"] for tool in response.json()["result"]["tools"]} == {"search_code", "get_chunk"}
        response = client.post("/mcp/", json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "get_chunk", "arguments": {"chunk_id": 1}},
        })
        assert response.status_code == 200
        assert response.json()["result"]["isError"] is False
        assert "fixture" in response.json()["result"]["structuredContent"]["result"]
        response = client.post("/mcp/", headers={"Host": "untrusted.example:8000"}, json={
            "jsonrpc": "2.0", "id": 3, "method": "tools/list",
        })
        assert response.status_code == 421
