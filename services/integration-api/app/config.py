import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    jobs_database_url: str
    jwt_secret: str
    sse_ping_seconds: float
    jobs_retention_hours: int
    job_max_seconds: float


def load_settings() -> Settings:
    """Read settings from the environment (invariant 7). Called only from
    the app lifespan, never at import time, so tests can construct Settings
    directly."""
    return Settings(
        jobs_database_url=os.environ["JOBS_DATABASE_URL"],
        jwt_secret=os.environ["JWT_SECRET"],
        sse_ping_seconds=float(os.getenv("SSE_PING_SECONDS", "15")),
        jobs_retention_hours=int(os.getenv("JOBS_RETENTION_HOURS", "24")),
        job_max_seconds=float(os.getenv("JOB_MAX_SECONDS", "120")),
    )
