"""In-memory JobStore test double with the same semantics as
PostgresJobStore (make test runs with --no-deps: no Postgres). State dicts
are attributes so tests can inspect/manipulate them (e.g. backdating
created_at for retention tests)."""
import uuid
from datetime import datetime, timedelta, timezone


def _iso(value):
    return value.isoformat() if value is not None else None


class MemoryJobStore:
    def __init__(self):
        self.jobs = {}
        self.plans = {}
        self.steps = {}   # (job_id, step_id) -> dict
        self.events = {}  # job_id -> list[dict payload]
        self.event_types = {}  # job_id -> list[str]

    async def create_job(self, user_sub, query):
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        self.jobs[job_id] = {
            "user_sub": user_sub, "query": query, "status": "planning",
            "created_at": now, "updated_at": now,
        }
        self.events[job_id] = []
        self.event_types[job_id] = []
        return job_id

    async def set_status(self, job_id, status):
        self.jobs[job_id]["status"] = status
        self.jobs[job_id]["updated_at"] = datetime.now(timezone.utc)

    async def append_event(self, job_id, seq, type, data):
        self.events[job_id].append(dict(data))
        self.event_types[job_id].append(type)

    async def save_plan(self, job_id, plan):
        self.plans[job_id] = plan

    async def upsert_step(self, job_id, step_id, status, *,
                          tool=None, args=None, result=None, error=None):
        key = (job_id, step_id)
        now = datetime.now(timezone.utc)
        step = self.steps.get(key) or {
            "step_id": step_id, "status": status, "tool": None, "args": None,
            "result": None, "error": None, "started_at": None, "finished_at": None,
        }
        step["status"] = status
        if tool is not None:
            step["tool"] = tool
        if args is not None:
            step["args"] = args
        if result is not None:
            step["result"] = result
        if error is not None:
            step["error"] = error
        if status == "running" and step["started_at"] is None:
            step["started_at"] = now
        if status in ("complete", "failed"):
            step["finished_at"] = now
        self.steps[key] = step

    async def get_job(self, job_id):
        job = self.jobs.get(job_id)
        if job is None:
            return None
        out = {
            "job_id": job_id,
            "status": job["status"],
            "query": job["query"],
            "created_at": _iso(job["created_at"]),
            "updated_at": _iso(job["updated_at"]),
            "steps": [],
            "answer": None,
        }
        if job_id in self.plans:
            out["plan"] = self.plans[job_id]
        out["steps"] = [
            {
                "step_id": s["step_id"], "status": s["status"], "tool": s["tool"],
                "args": s["args"], "result": s["result"], "error": s["error"],
                "started_at": _iso(s["started_at"]), "finished_at": _iso(s["finished_at"]),
            }
            for (jid, _), s in sorted(self.steps.items())
            if jid == job_id
        ]
        for payload, type in zip(reversed(self.events[job_id]),
                                 reversed(self.event_types[job_id])):
            if type == "answer" and out["answer"] is None:
                out["answer"] = payload.get("text")
            if type == "error" and "error" not in out:
                out["error"] = {"error": {"code": payload.get("code"),
                                          "message": payload.get("message")}}
        return out

    async def sweep(self, retention_hours):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
        doomed = [j for j, row in self.jobs.items() if row["created_at"] < cutoff]
        for job_id in doomed:
            del self.jobs[job_id]
            self.plans.pop(job_id, None)
            self.events.pop(job_id, None)
            self.event_types.pop(job_id, None)
            for key in [k for k in self.steps if k[0] == job_id]:
                del self.steps[key]
        return len(doomed)

    async def close(self):
        pass
