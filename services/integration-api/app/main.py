import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from agent import run_query
from shared.types import UserContext

from .auth import UnauthorizedError, require_user, unauthorized_handler
from .config import Settings, load_settings
from .jobstore import JobStore, PostgresJobStore
from .logging_setup import setup_json_logging
from .sink import JobEventSink

logger = logging.getLogger("integration_api")


class QueryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")  # openapi: additionalProperties false
    query: str = Field(min_length=1)


def sse_format(type: str, payload: dict) -> str:
    return f"event: {type}\ndata: {json.dumps(payload, default=str)}\n\n"


async def _run_job(
    store: JobStore, sink: JobEventSink, job_id: str,
    query: str, user: UserContext, job_max_seconds: float,
) -> None:
    outcome = "failed"
    try:
        async with asyncio.timeout(job_max_seconds):
            outcome = await run_query(query, user, sink)
    except TimeoutError:
        await sink.error("budget_exceeded", "job exceeded JOB_MAX_SECONDS wall clock")
    except Exception:
        logger.exception("job failed", extra={"ctx": {"job_id": job_id}})
        await sink.error("internal_error", "unexpected error; details logged server-side")
    final = "complete" if outcome == "complete" else "failed"
    await store.set_status(job_id, final)
    await sink.done(final)


def create_app(*, store: JobStore | None = None, settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_json_logging()
        app.state.settings = settings or load_settings()
        app.state.store = store or await PostgresJobStore.connect(
            app.state.settings.jobs_database_url
        )
        swept = await app.state.store.sweep(app.state.settings.jobs_retention_hours)
        logger.info("retention sweep", extra={"ctx": {"deleted_jobs": swept}})
        app.state.job_tasks = set()
        yield
        for task in app.state.job_tasks:
            task.cancel()
        await app.state.store.close()

    app = FastAPI(title="PSP7 Integration API", lifespan=lifespan)
    app.add_exception_handler(UnauthorizedError, unauthorized_handler)

    @app.get("/v1/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/query")
    async def submit_query(
        body: QueryBody, request: Request, user: UserContext = Depends(require_user)
    ) -> StreamingResponse:
        st = request.app.state
        job_id = await st.store.create_job(user.sub, body.query)
        logger.info("job created", extra={"ctx": {"job_id": job_id, "user_sub": user.sub}})
        queue: asyncio.Queue = asyncio.Queue()
        sink = JobEventSink(st.store, job_id, queue)
        await sink.job()  # first event, persisted before the stream opens
        task = asyncio.create_task(
            _run_job(st.store, sink, job_id, body.query, user, st.settings.job_max_seconds)
        )
        # The job survives client disconnect: the task is not tied to the
        # generator below; a dropped stream is recovered via GET /v1/jobs/{id}.
        st.job_tasks.add(task)
        task.add_done_callback(st.job_tasks.discard)

        async def stream():
            while True:
                try:
                    type_, payload = await asyncio.wait_for(
                        queue.get(), timeout=st.settings.sse_ping_seconds
                    )
                except TimeoutError:
                    await sink.ping()  # persisted too; arrives via the queue
                    continue
                yield sse_format(type_, payload)
                if type_ == "done":
                    return

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/v1/jobs/{job_id}")
    async def get_job(
        job_id: uuid.UUID, request: Request, user: UserContext = Depends(require_user)
    ):
        job = await request.app.state.store.get_job(str(job_id))
        if job is None:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "job_not_found",
                                   "message": "unknown or already-swept job_id"}},
            )
        return job

    return app


app = create_app()
