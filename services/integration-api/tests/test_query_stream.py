import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests import fake_agent
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
        chat_max_turns=20,
        chat_max_chars=32000,
    )
    app = create_app(store=store, settings=settings, run_query=fake_agent.run_query)
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
        chat_max_turns=20,
        chat_max_chars=32000,
    )
    app = create_app(store=store, settings=settings, run_query=fake_agent.run_query)
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
    async def rejected(body):
        resp = await lifespan_client.post("/v1/query", json=body, headers=auth_headers)
        assert resp.status_code == 422
        # 422s use the same {error: {code, message}} envelope as 401/404.
        assert resp.json()["error"]["code"] == "invalid_request"

    await rejected({"query": ""})
    await rejected({"query": "x", "extra": 1})
    await rejected({})
    await rejected({"query": "x", "messages": [{"role": "user", "content": "x"}]})
    await rejected({"messages": []})
    await rejected({"messages": [{"role": "system", "content": "x"}]})
    await rejected({"messages": [{"role": "user", "content": ""}]})
    await rejected({"messages": [{"role": "user", "content": "a"},
                                 {"role": "assistant", "content": "b"}]})


@pytest.mark.anyio
async def test_messages_request_streams_and_threads_history(auth_headers, monkeypatch):
    monkeypatch.setenv("AGENT_STUB_STEP_SECONDS", "0")
    seen = {}

    async def recording_run_query(query, user, sink, *, history=()):
        seen["query"], seen["history"] = query, list(history)
        return await fake_agent.run_query(query, user, sink, history=history)

    store = MemoryJobStore()
    settings = Settings(
        jobs_database_url="unused-in-tests",
        jwt_secret=TEST_SECRET,
        sse_ping_seconds=15.0,
        jobs_retention_hours=24,
        job_max_seconds=120.0,
        chat_max_turns=20,
        chat_max_chars=32000,
    )
    app = create_app(store=store, settings=settings, run_query=recording_run_query)
    convo = [
        {"role": "user", "content": "how many in FY2025?"},
        {"role": "assistant", "content": "66 total"},
        {"role": "user", "content": "compare to FY2024"},
    ]
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/query", json={"messages": convo}, headers=auth_headers)
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    assert events[-1][0] == "done" and events[-1][1]["status"] == "complete"
    assert seen["query"] == "compare to FY2024"
    assert seen["history"] == convo[:-1]
    job_id = events[0][1]["job_id"]
    assert store.jobs[job_id]["query"] == "compare to FY2024"
    assert store.jobs[job_id]["messages"] == convo


@pytest.mark.anyio
async def test_conversation_caps(auth_headers, monkeypatch):
    monkeypatch.setenv("AGENT_STUB_STEP_SECONDS", "0")
    settings = Settings(
        jobs_database_url="unused-in-tests",
        jwt_secret=TEST_SECRET,
        sse_ping_seconds=15.0,
        jobs_retention_hours=24,
        job_max_seconds=120.0,
        chat_max_turns=3,
        chat_max_chars=50,
    )
    app = create_app(store=MemoryJobStore(), settings=settings,
                     run_query=fake_agent.run_query)

    async def post(client, msgs):
        return await client.post("/v1/query", json={"messages": msgs}, headers=auth_headers)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            turn = {"role": "user", "content": "q"}
            resp = await post(client, [turn] * 4)  # > 3 turns
            assert resp.status_code == 422
            assert resp.json()["error"]["code"] == "conversation_too_large"
            resp = await post(client, [{"role": "user", "content": "x" * 51}])
            assert resp.status_code == 422
            assert resp.json()["error"]["code"] == "conversation_too_large"
            resp = await post(client, [turn] * 3)  # at the cap: fine
            assert resp.status_code == 200
