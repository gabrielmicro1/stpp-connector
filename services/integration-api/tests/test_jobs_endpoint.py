import uuid

import anyio

import pytest

from tests.conftest import parse_sse


@pytest.mark.anyio
async def test_unknown_job_404(lifespan_client, auth_headers):
    resp = await lifespan_client.get(f"/v1/jobs/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "job_not_found"


@pytest.mark.anyio
async def test_jobs_endpoint_requires_auth(lifespan_client):
    resp = await lifespan_client.get(f"/v1/jobs/{uuid.uuid4()}")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


@pytest.mark.anyio
async def test_completed_job_state_matches_stream(lifespan_client, auth_headers, monkeypatch):
    monkeypatch.setenv("AGENT_STUB_STEP_SECONDS", "0")
    resp = await lifespan_client.post("/v1/query", json={"query": "q"}, headers=auth_headers)
    events = parse_sse(resp.text)
    job_id = events[0][1]["job_id"]
    job = (await lifespan_client.get(f"/v1/jobs/{job_id}", headers=auth_headers)).json()
    assert job["job_id"] == job_id
    assert job["status"] == "complete"
    assert job["query"] == "q"
    assert job["plan"]["steps"][0]["tool"] == "search_proposals"
    assert [s["status"] for s in job["steps"]] == ["complete", "complete"]
    answer_event = next(p for k, p in events if k == "answer")
    assert job["answer"] == answer_event["text"]
    assert "error" not in job


@pytest.mark.anyio
async def test_mid_stream_state_is_consistent(lifespan_client, store, auth_headers, monkeypatch):
    """The write-then-emit guarantee: while the job is still mid-flight, GET
    /v1/jobs/{id} already shows the plan the stream carries. (httpx's
    ASGITransport buffers the response body, so the POST runs as a background
    task and the GET polls while the job executes; wire-level mid-stream
    behavior is exercised by the phase verify with real curl.)"""
    monkeypatch.setenv("AGENT_STUB_STEP_SECONDS", "0.4")
    results = {}

    async def post():
        results["resp"] = await lifespan_client.post(
            "/v1/query", json={"query": "q"}, headers=auth_headers
        )

    mid_flight_job = None
    async with anyio.create_task_group() as tg:
        tg.start_soon(post)
        # `store` is the same MemoryJobStore instance the app uses; the POST
        # hasn't returned yet (buffered), so discover the job_id through it,
        # then observe mid-flight state through the public GET endpoint.
        for _ in range(200):
            await anyio.sleep(0.02)
            if "resp" in results:
                break
            for job_id in list(store.jobs):
                job = (
                    await lifespan_client.get(f"/v1/jobs/{job_id}", headers=auth_headers)
                ).json()
                if "plan" in job:
                    mid_flight_job = job
                    break
            if mid_flight_job:
                break
        # task group waits here for the POST to finish and the stream to drain
    assert mid_flight_job is not None, "never observed the plan mid-flight"
    assert mid_flight_job["status"] in ("planning", "executing")

    events = parse_sse(results["resp"].text)
    plan_event = next(p for k, p in events if k == "plan")
    assert plan_event["plan"] == mid_flight_job["plan"]  # stream and store agree
    assert plan_event["job_id"] == mid_flight_job["job_id"]
