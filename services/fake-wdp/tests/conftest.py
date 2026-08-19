import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

TEST_TOKEN = "test-wdp-token"
DENIED_ORCID = "0000-0002-9999-0001"


class FakePool:
    """Stands in for an asyncpg pool at the network boundary of the tests:
    returns queued canned results in call order and records (sql, args)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple]] = []
        self._queue: list[object] = []

    def queue(self, result) -> None:
        self._queue.append(result)

    def _next(self, default):
        return self._queue.pop(0) if self._queue else default

    async def fetch(self, sql: str, *args):
        self.calls.append(("fetch", sql, args))
        return self._next([])

    async def fetchrow(self, sql: str, *args):
        self.calls.append(("fetchrow", sql, args))
        return self._next(None)

    async def close(self) -> None:
        pass


@pytest.fixture
def fake_pool():
    return FakePool()


@pytest.fixture
def settings():
    return Settings(
        wdp_database_url="unused-in-tests",
        wdp_fake_token=TEST_TOKEN,
        deny_orcids=frozenset({DENIED_ORCID}),
    )


@pytest.fixture
def app(settings, fake_pool):
    async def pool_factory(dsn):
        assert dsn == settings.wdp_database_url
        return fake_pool

    return create_app(settings=settings, pool_factory=pool_factory)


@pytest.fixture
def client(app):
    # Context manager runs the lifespan so app.state.pool is the FakePool.
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {TEST_TOKEN}"}
