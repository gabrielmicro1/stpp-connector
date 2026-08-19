"""Job store: state tables in the `jobs` database (write-then-emit source
of truth for both the SSE stream and GET /v1/jobs/{job_id}).

PostgresJobStore's SQL is exercised by the phase verify against real
containers (`make up` + curl); unit tests run with --no-deps and use
tests/memory_store.MemoryJobStore behind the same Protocol.
"""
import json
from datetime import datetime
from typing import Protocol

import asyncpg


class JobStore(Protocol):
    async def create_job(self, user_sub: str, query: str) -> str: ...
    async def set_status(self, job_id: str, status: str) -> None: ...
    async def append_event(self, job_id: str, seq: int, type: str, data: dict) -> None: ...
    async def save_plan(self, job_id: str, plan: dict) -> None: ...
    async def upsert_step(
        self, job_id: str, step_id: int, status: str, *,
        tool: str | None = None, args: dict | None = None,
        result: object = None, error: str | None = None,
    ) -> None: ...
    async def get_job(self, job_id: str) -> dict | None: ...
    async def sweep(self, retention_hours: int) -> int: ...
    async def close(self) -> None: ...


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class PostgresJobStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> "PostgresJobStore":
        return cls(await asyncpg.create_pool(dsn))

    async def create_job(self, user_sub: str, query: str) -> str:
        row = await self._pool.fetchrow(
            "INSERT INTO jobs (user_sub, query, status) VALUES ($1, $2, 'planning')"
            " RETURNING job_id",
            user_sub, query,
        )
        return str(row["job_id"])

    async def set_status(self, job_id: str, status: str) -> None:
        await self._pool.execute(
            "UPDATE jobs SET status = $2, updated_at = now() WHERE job_id = $1",
            job_id, status,
        )

    async def append_event(self, job_id: str, seq: int, type: str, data: dict) -> None:
        await self._pool.execute(
            "INSERT INTO job_events (job_id, seq, type, data) VALUES ($1, $2, $3, $4::jsonb)",
            job_id, seq, type, json.dumps(data),
        )

    async def save_plan(self, job_id: str, plan: dict) -> None:
        await self._pool.execute(
            """INSERT INTO job_plans (job_id, plan, validated_at)
               VALUES ($1, $2::jsonb, now())
               ON CONFLICT (job_id) DO UPDATE
               SET plan = EXCLUDED.plan, validated_at = EXCLUDED.validated_at""",
            job_id, json.dumps(plan),
        )

    async def upsert_step(
        self, job_id: str, step_id: int, status: str, *,
        tool: str | None = None, args: dict | None = None,
        result: object = None, error: str | None = None,
    ) -> None:
        # tool is NOT NULL in the schema: the first write for a step (its
        # `running` transition) must include it; later partial updates
        # COALESCE so they never null earlier fields.
        await self._pool.execute(
            """INSERT INTO job_steps
                   (job_id, step_id, status, tool, args, result, error,
                    started_at, finished_at)
               VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7,
                       CASE WHEN $3 = 'running' THEN now() END,
                       CASE WHEN $3 IN ('complete', 'failed') THEN now() END)
               ON CONFLICT (job_id, step_id) DO UPDATE SET
                   status = EXCLUDED.status,
                   tool = COALESCE(EXCLUDED.tool, job_steps.tool),
                   args = COALESCE(EXCLUDED.args, job_steps.args),
                   result = COALESCE(EXCLUDED.result, job_steps.result),
                   error = COALESCE(EXCLUDED.error, job_steps.error),
                   started_at = COALESCE(job_steps.started_at, EXCLUDED.started_at),
                   finished_at = COALESCE(EXCLUDED.finished_at, job_steps.finished_at)""",
            job_id, step_id, status, tool,
            json.dumps(args) if args is not None else None,
            json.dumps(result) if result is not None else None,
            error,
        )

    async def get_job(self, job_id: str) -> dict | None:
        async with self._pool.acquire() as conn:
            job = await conn.fetchrow(
                "SELECT job_id, status, query, created_at, updated_at"
                " FROM jobs WHERE job_id = $1",
                job_id,
            )
            if job is None:
                return None
            out = {
                "job_id": str(job["job_id"]),
                "status": job["status"],
                "query": job["query"],
                "created_at": _iso(job["created_at"]),
                "updated_at": _iso(job["updated_at"]),
                "steps": [],
                "answer": None,
            }
            plan = await conn.fetchval(
                "SELECT plan FROM job_plans WHERE job_id = $1", job_id
            )
            if plan is not None:
                out["plan"] = json.loads(plan)
            steps = await conn.fetch(
                "SELECT step_id, status, tool, args, result, error,"
                " started_at, finished_at"
                " FROM job_steps WHERE job_id = $1 ORDER BY step_id",
                job_id,
            )
            out["steps"] = [
                {
                    "step_id": s["step_id"],
                    "status": s["status"],
                    "tool": s["tool"],
                    "args": json.loads(s["args"]) if s["args"] is not None else None,
                    "result": json.loads(s["result"]) if s["result"] is not None else None,
                    "error": s["error"],
                    "started_at": _iso(s["started_at"]),
                    "finished_at": _iso(s["finished_at"]),
                }
                for s in steps
            ]
            answer = await conn.fetchval(
                "SELECT data FROM job_events"
                " WHERE job_id = $1 AND type = 'answer' ORDER BY seq DESC LIMIT 1",
                job_id,
            )
            if answer is not None:
                out["answer"] = json.loads(answer).get("text")
            error = await conn.fetchval(
                "SELECT data FROM job_events"
                " WHERE job_id = $1 AND type = 'error' ORDER BY seq DESC LIMIT 1",
                job_id,
            )
            if error is not None:
                payload = json.loads(error)
                out["error"] = {
                    "error": {"code": payload.get("code"), "message": payload.get("message")}
                }
            return out

    async def sweep(self, retention_hours: int) -> int:
        tag = await self._pool.execute(
            "DELETE FROM jobs WHERE created_at < now() - make_interval(hours => $1)",
            retention_hours,
        )
        return int(tag.rsplit(" ", 1)[-1])

    async def close(self) -> None:
        await self._pool.close()
