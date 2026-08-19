from tests.conftest import make_token


def _post(client, headers):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers=headers,
    )


def test_missing_token_is_401(client):
    resp = _post(client, {})
    assert resp.status_code == 401
    assert resp.json() == {
        "error": {"code": "unauthorized", "message": "missing bearer token"}
    }
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_wrong_secret_is_401(client):
    resp = _post(
        client, {"Authorization": f"Bearer {make_token(secret='other-secret')}"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_expired_token_is_401(client):
    resp = _post(client, {"Authorization": f"Bearer {make_token(exp_delta=-10)}"})
    assert resp.status_code == 401


def test_missing_sub_is_401(client):
    import jwt as pyjwt
    import time

    stripped = pyjwt.encode(
        {"exp": int(time.time()) + 3600}, "test-secret", algorithm="HS256"
    )
    resp = _post(client, {"Authorization": f"Bearer {stripped}"})
    assert resp.status_code == 401


def test_healthz_needs_no_token(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
