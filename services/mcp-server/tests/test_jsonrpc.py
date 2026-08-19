from tests.conftest import make_token


def test_parse_error(client):
    resp = client.post(
        "/mcp",
        content=b"not json",
        headers={
            "Authorization": f"Bearer {make_token()}",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32700
    assert body["id"] is None


def test_missing_method_is_invalid_request(client):
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 7},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.json()["error"]["code"] == -32600
    assert resp.json()["id"] == 7


def test_wrong_version_is_invalid_request(client):
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "1.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.json()["error"]["code"] == -32600


def test_unknown_method(rpc):
    body = rpc(make_token(), "tools/destroy", rpc_id=42).json()
    assert body["error"]["code"] == -32601
    assert body["id"] == 42


def test_non_object_params_rejected(client):
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": [1]},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.json()["error"]["code"] == -32600
