import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests.conftest import TEST_SECRET, parse_sse
from tests.memory_store import MemoryJobStore


@pytest.mark.anyio
async def test_stream_event_order_and_seq(lifespan_client, auth_headers, monkeypatch):
    monkeypatch.setenv("AGENT_STUB_STEP_SECONDS", "0")
    resp = await lifespan_client.post("/v1/query", json={"query": "test"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(resp.text)

    kinds = [k for k, _ in events]
    assert kinds[0] == "job" and events[0][1]["status"] == "planning"
    assert kinds[-1] == "done" and events[-1][1]["status"] == "complete"
    assert "plan" in kinds and "answer" in kinds

    step_events = [p for k, p in events if k == "step"]
    assert [s["status"] for s in step_events] == ["running", "complete", "running", "complete"]
    completes = [s for s in step_events if s["status"] == "complete"]
    assert all("rows" in s and "truncated" in s and s["summary"] for s in completes)

    seqs = [p["seq"] for _, p in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert len({p["job_id"] for _, p in events}) == 1

    plan_event = next(p for k, p in events if k == "plan")
    assert plan_event["plan"]["steps"][0]["tool"] == "search_proposals"


@pytest.mark.anyio
async def test_heartbeat_pings_flow(auth_headers, monkeypatch):
    # slow stub + tiny ping interval -> pings appear between events
    monkeypatch.setenv("AGENT_STUB_STEP_SECONDS", "0.3")
    store = MemoryJobStore()
    settings = Settings(
        jobs_database_url="unused-in-tests",
        jwt_secret=TEST_SECRET,
        sse_ping_seconds=0.05,
        jobs_retention_hours=24,
        job_max_seconds=120.0,
    )
    app = create_app(store=store, settings=settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/query", json={"query": "q"}, headers=auth_headers)
    events = parse_sse(resp.text)
    pings = [p for k, p in events if k == "ping"]
    assert pings, "expected at least one ping heartbeat"
    job_id = events[0][1]["job_id"]
    assert all(p["job_id"] == job_id and "seq" in p for p in pings)
    seqs = [p["seq"] for _, p in events]
    assert seqs == sorted(seqs)
    # pings are persisted too (write-then-emit applies to every event)
    assert "ping" in store.event_types[job_id]


@pytest.mark.anyio
async def test_budget_exceeded_wall_clock(auth_headers, monkeypatch):
    monkeypatch.setenv("AGENT_STUB_STEP_SECONDS", "5")
    store = MemoryJobStore()
    settings = Settings(
        jobs_database_url="unused-in-tests",
        jwt_secret=TEST_SECRET,
        sse_ping_seconds=15.0,
        jobs_retention_hours=24,
        job_max_seconds=0.1,
    )
    app = create_app(store=store, settings=settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/query", json={"query": "q"}, headers=auth_headers)
    events = parse_sse(resp.text)
    error = next(p for k, p in events if k == "error")
    assert error["code"] == "budget_exceeded"
    assert events[-1][0] == "done" and events[-1][1]["status"] == "failed"
    job_id = events[0][1]["job_id"]
    assert store.jobs[job_id]["status"] == "failed"


@pytest.mark.anyio
async def test_query_body_validation(lifespan_client, auth_headers):
    resp = await lifespan_client.post("/v1/query", json={"query": ""}, headers=auth_headers)
    assert resp.status_code == 422
    resp = await lifespan_client.post(
        "/v1/query", json={"query": "x", "extra": 1}, headers=auth_headers
    )
    assert resp.status_code == 422
    resp = await lifespan_client.post("/v1/query", json={}, headers=auth_headers)
    assert resp.status_code == 422
