import json
import time

import httpx
import jwt
import pytest

from app.config import Settings
from app.main import create_app
from tests.memory_store import MemoryJobStore

TEST_SECRET = "test-secret"


def make_token(secret=TEST_SECRET, exp_delta=3600, **overrides):
    claims = {
        "sub": "analyst-full",
        "name": "Avery Fullaccess",
        "component": "DARPA",
        "roles": ["rfff_reader", "wdp_reader"],
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_delta,
    }
    claims.update(overrides)
    return jwt.encode(claims, secret, algorithm="HS256")


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def settings():
    return Settings(
        jobs_database_url="unused-in-tests",
        jwt_secret=TEST_SECRET,
        sse_ping_seconds=15.0,
        jobs_retention_hours=24,
        job_max_seconds=120.0,
    )


@pytest.fixture
def store():
    return MemoryJobStore()


@pytest.fixture
def app(store, settings):
    return create_app(store=store, settings=settings)


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {make_token()}"}


@pytest.fixture
async def lifespan_client(app):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
