import pytest

from app.enums import ObservedEnumCache

from tests.conftest import FakePool

pytestmark = pytest.mark.anyio


async def test_loads_and_caches(monkeypatch):
    pool = FakePool()
    pool.fetch_results.append(
        [
            {"field_name": "fiscal_year", "value": "2023"},
            {"field_name": "fiscal_year", "value": "2024"},
            {"field_name": "award_state", "value": "Awarded"},
        ]
    )
    cache = ObservedEnumCache(lambda: pool, ttl_seconds=60)
    observed = await cache.get()
    assert observed == {"fiscal_year": {"2023", "2024"}, "award_state": {"Awarded"}}
    await cache.get()
    assert len(pool.calls) == 1  # served from cache inside the TTL


async def test_serves_last_known_on_failure():
    class ExplodingPool:
        async def fetch(self, sql):
            raise RuntimeError("db down")

    cache = ObservedEnumCache(lambda: ExplodingPool(), ttl_seconds=0)
    assert await cache.get() == {}  # never raises pre-seed
    cache._cached = {"fiscal_year": {"2023"}}
    assert await cache.get() == {"fiscal_year": {"2023"}}
