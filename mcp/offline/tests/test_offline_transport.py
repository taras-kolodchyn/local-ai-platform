import os

from starlette.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@127.0.0.1/unused")

from local_ai_offline_mcp import service


def test_mounted_transport_preserves_read_only_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "ROOT", tmp_path.resolve())
    (tmp_path / "README.md").write_text("fixture", encoding="utf-8")
    (tmp_path / ".env").write_text("private fixture", encoding="utf-8")
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    with TestClient(service.app, base_url="http://127.0.0.1:8001", headers=headers) as client:
        response = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert response.status_code == 200
        assert {tool["name"] for tool in response.json()["result"]["tools"]} == {
            "list_files", "read_text_file", "git_status", "git_log", "postgres_select",
        }
        for path, is_error in [("README.md", False), (".env", True)]:
            response = client.post("/mcp/", json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "read_text_file", "arguments": {"path": path}},
            })
            assert response.status_code == 200
            assert response.json()["result"]["isError"] is is_error
        response = client.post("/mcp/", headers={"Host": "untrusted.example:8001"}, json={
            "jsonrpc": "2.0", "id": 3, "method": "tools/list",
        })
        assert response.status_code == 421
