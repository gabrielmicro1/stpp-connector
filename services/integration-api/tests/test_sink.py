import asyncio

import pytest

from app.sink import JobEventSink
from tests.memory_store import MemoryJobStore


@pytest.mark.anyio
async def test_write_then_emit_order_and_seq():
    store = MemoryJobStore()
    job_id = await store.create_job("u", "q")
    queue = asyncio.Queue()
    sink = JobEventSink(store, job_id, queue)
    await sink.job()
    await sink.plan({"intent": "i", "steps": []})
    await sink.step(1, "running", tool="t", args={})
    await sink.ping()
    await sink.answer("text")
    await sink.done("complete")

    seqs = [e["seq"] for e in store.events[job_id]]
    assert seqs == [1, 2, 3, 4, 5, 6]  # persisted, monotonic, gap-free (pings included)
    assert store.event_types[job_id] == ["job", "plan", "step", "ping", "answer", "done"]

    emitted = []
    while not queue.empty():
        emitted.append(queue.get_nowait())
    assert [t for t, _ in emitted] == ["job", "plan", "step", "ping", "answer", "done"]
    assert all(p["job_id"] == job_id for _, p in emitted)
    # write-then-emit: everything on the queue is already in the store
    assert len(store.events[job_id]) == len(emitted)
    assert emitted[0][1]["status"] == "planning"
    assert emitted[1][1]["plan"] == {"intent": "i", "steps": []}
    assert emitted[-1][1]["status"] == "complete"


@pytest.mark.anyio
async def test_step_event_carries_only_sse_fields():
    store = MemoryJobStore()
    job_id = await store.create_job("u", "q")
    sink = JobEventSink(store, job_id, asyncio.Queue())
    await sink.step(1, "complete", tool="t", args={"a": 1}, result={"data": []},
                    summary="s", rows=0, truncated=False)
    event = store.events[job_id][0]
    assert set(event) == {"job_id", "seq", "step_id", "status", "summary", "rows", "truncated"}
    assert store.steps[(job_id, 1)]["result"] == {"data": []}  # result persisted to job_steps only
    assert store.steps[(job_id, 1)]["tool"] == "t"


@pytest.mark.anyio
async def test_status_updates_job_row_without_event():
    store = MemoryJobStore()
    job_id = await store.create_job("u", "q")
    sink = JobEventSink(store, job_id, asyncio.Queue())
    await sink.status("executing")
    assert store.jobs[job_id]["status"] == "executing"
    assert store.events[job_id] == []


@pytest.mark.anyio
async def test_concurrent_emitters_keep_seq_unique():
    store = MemoryJobStore()
    job_id = await store.create_job("u", "q")
    sink = JobEventSink(store, job_id, asyncio.Queue())

    async def spam_pings():
        for _ in range(20):
            await sink.ping()

    await asyncio.gather(spam_pings(), spam_pings())
    seqs = [e["seq"] for e in store.events[job_id]]
    assert seqs == list(range(1, 41))
