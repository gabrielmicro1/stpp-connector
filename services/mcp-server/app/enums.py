"""Observed enum sets from rfff_seed, cached with a TTL.

Lazy on purpose: `make seed` runs after `make up`, so a startup-only
snapshot would be empty forever. On query failure the last-known sets are
served (or {} before the first success) — tools/list must not 500 just
because seeding hasn't happened yet.
"""
import logging
import time
from typing import Callable

logger = logging.getLogger("mcp.enums")


class ObservedEnumCache:
    def __init__(self, pool_getter: Callable, ttl_seconds: float) -> None:
        self._pool_getter = pool_getter
        self._ttl = ttl_seconds
        self._cached: dict[str, set[str]] = {}
        self._fetched_at: float | None = None

    async def get(self) -> dict[str, set[str]]:
        now = time.monotonic()
        if self._fetched_at is not None and now - self._fetched_at < self._ttl:
            return self._cached
        try:
            rows = await self._pool_getter().fetch(
                "SELECT field_name, value FROM observed_enums"
            )
            observed: dict[str, set[str]] = {}
            for row in rows:
                observed.setdefault(row["field_name"], set()).add(row["value"])
            self._cached = observed
        except Exception as exc:
            logger.warning(
                "observed_enums load failed; serving last-known sets",
                extra={"ctx": {"error": str(exc)}},
            )
        self._fetched_at = now
        return self._cached
