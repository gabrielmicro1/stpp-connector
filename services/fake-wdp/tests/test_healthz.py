def test_healthz_returns_ok(client):
    resp = client.get("/v1/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_healthz_requires_no_token(client):
    # Compose healthcheck curls without a bearer token.
    assert client.get("/v1/healthz").status_code == 200
