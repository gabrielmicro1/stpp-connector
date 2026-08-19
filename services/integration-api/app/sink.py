import asyncio

from .jobstore import JobStore


class JobEventSink:
    """Implements agent.EventSink. Write-then-emit: every event is persisted
    to job_events BEFORE being pushed to the SSE queue, so GET /v1/jobs/{id}
    can never be behind the stream. The lock makes seq assignment + persist
    + enqueue atomic, so the generator's concurrent ping() can never reorder
    seq on the wire."""

    def __init__(self, store: JobStore, job_id: str, queue: asyncio.Queue) -> None:
        self._store = store
        self._job_id = job_id
        self._queue = queue
        self._seq = 0
        self._lock = asyncio.Lock()

    async def _emit(self, type: str, fields: dict) -> None:
        async with self._lock:
            self._seq += 1
            payload = {"job_id": self._job_id, "seq": self._seq, **fields}
            await self._store.append_event(self._job_id, self._seq, type, payload)
            await self._queue.put((type, payload))

    # --- app-side events (not part of agent.EventSink) ---

    async def job(self) -> None:
        await self._emit("job", {"status": "planning"})

    async def ping(self) -> None:
        await self._emit("ping", {})

    async def done(self, status: str) -> None:
        await self._emit("done", {"status": status})

    # --- agent.EventSink protocol ---

    async def status(self, status: str) -> None:
        await self._store.set_status(self._job_id, status)

    async def plan(self, plan: dict) -> None:
        await self._store.save_plan(self._job_id, plan)
        await self._emit("plan", {"plan": plan})

    async def step(
        self, step_id: int, status: str, *,
        tool: str | None = None, args: dict | None = None, result: object = None,
        summary: str | None = None, rows: int | None = None,
        truncated: bool | None = None, error: str | None = None,
    ) -> None:
        await self._store.upsert_step(
            self._job_id, step_id, status,
            tool=tool, args=args, result=result, error=error,
        )
        fields: dict = {"step_id": step_id, "status": status}
        for key, value in (("summary", summary), ("rows", rows), ("truncated", truncated)):
            if value is not None:
                fields[key] = value
        await self._emit("step", fields)

    async def answer(self, text: str) -> None:
        await self._emit("answer", {"text": text})

    async def error(self, code: str, message: str) -> None:
        await self._emit("error", {"code": code, "message": message})
