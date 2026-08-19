from fastapi.testclient import TestClient

from app.main import app


def test_healthz_returns_ok():
    client = TestClient(app)
    resp = client.get("/v1/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
