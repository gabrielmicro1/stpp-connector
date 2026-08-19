import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_token


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def _post(client, headers):
    return client.post("/v1/query", json={"query": "x"}, headers=headers)


def test_missing_token_401(client):
    resp = _post(client, {})
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "unauthorized"
    assert body["error"]["message"]
    assert resp.headers["www-authenticate"] == "Bearer"


def test_bad_signature_401(client):
    resp = _post(client, {"Authorization": f"Bearer {make_token(secret='wrong')}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_expired_token_401(client):
    resp = _post(client, {"Authorization": f"Bearer {make_token(exp_delta=-10)}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_garbage_token_401(client):
    resp = _post(client, {"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


def test_healthz_needs_no_auth(client):
    assert client.get("/v1/healthz").status_code == 200


@pytest.mark.anyio
async def test_user_context_carries_raw_token(app, settings):
    from types import SimpleNamespace

    from fastapi.security import HTTPAuthorizationCredentials

    from app.auth import require_user

    token = make_token()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    user = await require_user(request, creds)
    assert user.token == token


def test_user_context_repr_excludes_token():
    from shared.types import UserContext

    user = UserContext(
        sub="s", name="n", component="c", roles=("rfff_reader",), token="secret-jwt"
    )
    assert "secret-jwt" not in repr(user)
