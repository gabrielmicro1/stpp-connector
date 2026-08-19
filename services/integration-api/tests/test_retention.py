from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.main import create_app
from tests.conftest import TEST_SECRET
from tests.memory_store import MemoryJobStore


@pytest.mark.anyio
async def test_sweep_deletes_only_old_jobs():
    store = MemoryJobStore()
    old = await store.create_job("analyst-full", "old query")
    new = await store.create_job("analyst-full", "new query")
    store.jobs[old]["created_at"] = datetime.now(timezone.utc) - timedelta(hours=48)
    deleted = await store.sweep(retention_hours=24)
    assert deleted == 1
    assert await store.get_job(old) is None
    assert (await store.get_job(new))["query"] == "new query"


@pytest.mark.anyio
async def test_sweep_cascades_child_rows():
    store = MemoryJobStore()
    old = await store.create_job("u", "q")
    await store.save_plan(old, {"intent": "i", "steps": []})
    await store.upsert_step(old, 1, "running", tool="t", args={})
    await store.append_event(old, 1, "job", {"job_id": old, "seq": 1, "status": "planning"})
    store.jobs[old]["created_at"] = datetime.now(timezone.utc) - timedelta(hours=48)
    await store.sweep(retention_hours=24)
    assert old not in store.plans
    assert (old, 1) not in store.steps
    assert old not in store.events


@pytest.mark.anyio
async def test_get_job_assembles_full_shape():
    store = MemoryJobStore()
    job_id = await store.create_job("analyst-full", "q")
    await store.save_plan(job_id, {"intent": "i", "steps": []})
    await store.upsert_step(job_id, 1, "running", tool="search_proposals", args={"filters": {}})
    await store.upsert_step(job_id, 1, "complete", result={"data": []})
    await store.append_event(job_id, 7, "answer", {"job_id": job_id, "seq": 7, "text": "hi"})
    await store.set_status(job_id, "complete")
    job = await store.get_job(job_id)
    assert job["status"] == "complete"
    assert job["query"] == "q"
    assert job["plan"]["intent"] == "i"
    step = job["steps"][0]
    assert step["tool"] == "search_proposals"      # COALESCE survived the update
    assert step["args"] == {"filters": {}}
    assert step["status"] == "complete"
    assert step["result"] == {"data": []}
    assert step["started_at"] is not None
    assert step["finished_at"] is not None
    assert job["answer"] == "hi"
    assert "error" not in job
    assert job["created_at"] and job["updated_at"]


@pytest.mark.anyio
async def test_get_job_error_and_null_answer():
    store = MemoryJobStore()
    job_id = await store.create_job("u", "q")
    await store.append_event(
        job_id, 2, "error",
        {"job_id": job_id, "seq": 2, "code": "internal_error", "message": "boom"},
    )
    await store.set_status(job_id, "failed")
    job = await store.get_job(job_id)
    assert job["answer"] is None
    assert job["error"] == {"error": {"code": "internal_error", "message": "boom"}}


@pytest.mark.anyio
async def test_get_job_unknown_returns_none():
    store = MemoryJobStore()
    assert await store.get_job("00000000-0000-0000-0000-000000000000") is None


@pytest.mark.anyio
async def test_startup_lifespan_runs_retention_sweep():
    store = MemoryJobStore()
    old = await store.create_job("u", "old query")
    store.jobs[old]["created_at"] = datetime.now(timezone.utc) - timedelta(hours=48)
    settings = Settings(
        jobs_database_url="unused-in-tests",
        jwt_secret=TEST_SECRET,
        sse_ping_seconds=15.0,
        jobs_retention_hours=24,
        job_max_seconds=120.0,
    )
    app = create_app(store=store, settings=settings)
    async with app.router.lifespan_context(app):
        assert old not in store.jobs  # swept at startup
