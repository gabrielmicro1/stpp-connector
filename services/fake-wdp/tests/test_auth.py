import pytest

UNAUTHORIZED = {"error": {"code": "unauthorized", "message": "missing or invalid bearer token"}}


@pytest.mark.parametrize(
    "path", ["/v1/persons", "/v1/entities", "/v1/documents/some-ref"]
)
def test_missing_token_is_401(client, path):
    resp = client.get(path)
    assert resp.status_code == 401
    assert resp.json() == UNAUTHORIZED


def test_wrong_token_is_401(client):
    resp = client.get("/v1/persons", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401
    assert resp.json() == UNAUTHORIZED


def test_non_bearer_scheme_is_401(client, auth_headers):
    token = auth_headers["Authorization"].split(" ", 1)[1]
    resp = client.get("/v1/persons", headers={"Authorization": f"Basic {token}"})
    assert resp.status_code == 401


def test_no_sql_runs_when_unauthorized(client, fake_pool):
    client.get("/v1/persons")
    assert fake_pool.calls == []
