import logging
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, find_contracts_dir
from app.main import create_app

SECRET = "test-secret"
FULL_ROLES = ("rfff_reader", "wdp_reader")
LOCAL_ROLES = ("rfff_reader",)

# Seed-shaped observed sets; the real ones come from rfff_seed at runtime.
OBSERVED = {
    "fiscal_year": {"2023", "2024"},
    "assessment_state": {"Complete", "Implemented"},
}


def make_token(secret: str = SECRET, exp_delta: int = 3600, **overrides) -> str:
    claims = {
        "sub": "analyst-full",
        "name": "Avery Fullaccess",
        "component": "DARPA",
        "roles": list(FULL_ROLES),
        "exp": int(time.time()) + exp_delta,
    }
    claims.update(overrides)
    return jwt.encode(claims, secret, algorithm="HS256")


class FakePool:
    """Queued canned results; records (kind, sql, args) calls."""

    def __init__(self) -> None:
        self.fetch_results: list = []
        self.fetchrow_results: list = []
        self.calls: list[tuple] = []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.fetch_results.pop(0) if self.fetch_results else []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self.fetchrow_results.pop(0) if self.fetchrow_results else None


class FixedEnums:
    def __init__(self, observed: dict) -> None:
        self._observed = observed

    async def get(self) -> dict:
        return self._observed


class StubWDP:
    """Queued results per call; an Exception item is raised instead."""

    def __init__(self) -> None:
        self.results: list = []
        self.calls: list[tuple] = []

    async def _next(self, method: str, kwargs: dict):
        self.calls.append((method, kwargs))
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def persons(self, **kwargs):
        return await self._next("persons", kwargs)

    async def entities(self, **kwargs):
        return await self._next("entities", kwargs)

    async def documents(self, ref_id, **kwargs):
        return await self._next("documents", {"ref_id": ref_id, **kwargs})


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def contracts_dir():
    return find_contracts_dir()


@pytest.fixture
def settings(contracts_dir):
    return Settings(
        rfff_seed_database_url="postgresql://unused",
        jwt_secret=SECRET,
        wdp_base_url="http://wdp.test",
        wdp_auth_token="wdp-test-token",
        contracts_dir=contracts_dir,
    )


@pytest.fixture
def fake_pool():
    return FakePool()


@pytest.fixture
def stub_wdp():
    return StubWDP()


@pytest.fixture
def app(settings, fake_pool, stub_wdp):
    async def pool_factory(dsn):
        return fake_pool

    return create_app(
        settings=settings,
        pool_factory=pool_factory,
        wdp_client=stub_wdp,
        enum_cache=FixedEnums(OBSERVED),
    )


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def rpc(client):
    def _rpc(token: str, method: str, params: dict | None = None, rpc_id=1):
        return client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}},
            headers={"Authorization": f"Bearer {token}"},
        )

    return _rpc


@pytest.fixture
def audit_records():
    """Capture mcp.audit records directly: setup_json_logging replaces root
    handlers during the app lifespan, which breaks caplog's root capture."""
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append
    audit_logger = logging.getLogger("mcp.audit")
    audit_logger.addHandler(handler)
    yield records
    audit_logger.removeHandler(handler)
