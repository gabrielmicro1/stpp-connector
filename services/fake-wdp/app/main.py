"""fake-wdp: demo-only stand-in for the WDP Query Interface.

Speaks the HTTP contract in docs/specs/fake-wdp.md over the `wdp` db so the
MCP server's WDPClient uses real HTTP in demo and prod (invariant 4).
Simulates WDP-side failure modes: ?_delay=<seconds> and FAKE_WDP_DENY_ORCIDS.
"""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from .config import Settings, load_settings
from .logging_setup import setup_json_logging

logger = logging.getLogger("fake_wdp")

# Cap for ?_delay so a typo can't wedge the demo for minutes.
MAX_DELAY_SECONDS = 30.0


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


async def require_token(request: Request) -> None:
    """Presence-check of the single static bearer token; the real per-user
    enforcement being demoed lives in the MCP server (spec: fake-wdp)."""
    expected = f"Bearer {request.app.state.settings.wdp_fake_token}"
    if request.headers.get("authorization") != expected:
        raise ApiError(401, "unauthorized", "missing or invalid bearer token")


async def apply_delay(
    _delay: float | None = Query(default=None, alias="_delay"),
) -> None:
    """Scripted slow-response hook: sleep before responding so SSE progress
    can be seen genuinely streaming during a slow step."""
    if _delay is not None and _delay > 0:
        await asyncio.sleep(min(_delay, MAX_DELAY_SECONDS))


def _persons_sql(orcid: str | None, name: str | None, limit: int | None):
    """Summaries + counts only; document detail is behind /v1/documents
    (deliberate cheap-discovery / expensive-retrieval asymmetry)."""
    where: list[str] = []
    args: list[object] = []
    if orcid is not None:
        args.append(orcid)
        where.append(f"p.orcid = ${len(args)}")
    if name is not None:
        args.append(name)
        where.append(f"p.name ILIKE '%' || ${len(args)} || '%'")
    sql = (
        "SELECT p.ref_id, p.orcid, p.name, p.affiliations,"
        " COUNT(d.doc_id) FILTER (WHERE d.type = 'publication') AS publication_count,"
        " COUNT(d.doc_id) FILTER (WHERE d.type = 'funding_record') AS funding_count,"
        " COUNT(*) OVER () AS total"
        " FROM persons p LEFT JOIN documents d ON d.ref_id = p.ref_id"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY p.ref_id ORDER BY p.orcid"
    if limit is not None:
        args.append(max(limit, 1))
        sql += f" LIMIT ${len(args)}"
    return sql, args


def _entities_sql(uei: str | None, name: str | None, limit: int | None):
    where: list[str] = []
    args: list[object] = []
    if uei is not None:
        args.append(uei)
        where.append(f"e.uei = ${len(args)}")
    if name is not None:
        args.append(name)
        where.append(f"e.name ILIKE '%' || ${len(args)} || '%'")
    sql = (
        "SELECT e.ref_id, e.uei, e.name, e.country,"
        " COUNT(d.doc_id) AS record_count,"
        " COUNT(*) OVER () AS total"
        " FROM entities e LEFT JOIN documents d ON d.ref_id = e.ref_id"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY e.ref_id ORDER BY e.uei"
    if limit is not None:
        args.append(max(limit, 1))
        sql += f" LIMIT ${len(args)}"
    return sql, args


def _documents_sql(ref_id: uuid.UUID, limit: int | None):
    args: list[object] = [ref_id]
    sql = (
        "SELECT doc_id, type, title, year, source, detail,"
        " COUNT(*) OVER () AS total"
        " FROM documents WHERE ref_id = $1"
        " ORDER BY year DESC NULLS LAST, doc_id"
    )
    if limit is not None:
        args.append(max(limit, 1))
        sql += f" LIMIT ${len(args)}"
    return sql, args


def _decode_detail(detail: object) -> object:
    # asyncpg returns jsonb as str unless a codec is registered; the response
    # must carry a JSON object, not a string.
    if isinstance(detail, str):
        return json.loads(detail)
    return detail


def create_app(*, settings: Settings | None = None, pool_factory=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_json_logging()
        app.state.settings = settings or load_settings()
        if pool_factory is not None:
            app.state.pool = await pool_factory(app.state.settings.wdp_database_url)
        else:
            import asyncpg  # deferred: tests never import it

            app.state.pool = await asyncpg.create_pool(app.state.settings.wdp_database_url)
        logger.info(
            "fake-wdp up",
            extra={"ctx": {"deny_orcids": len(app.state.settings.deny_orcids)}},
        )
        yield
        await app.state.pool.close()

    app = FastAPI(title="fake-wdp (demo-only WDP stand-in)", lifespan=lifespan)
    app.add_exception_handler(ApiError, api_error_handler)

    @app.get("/v1/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # Auth first (no sleeping for unauthenticated callers), then the delay hook.
    data_deps = [Depends(require_token), Depends(apply_delay)]

    @app.get("/v1/persons", dependencies=data_deps)
    async def persons(
        request: Request,
        orcid: str | None = None,
        name: str | None = None,
        limit: int | None = None,
    ) -> dict:
        st = request.app.state
        # Deny applies only to explicit orcid targeting, not name searches:
        # demo Query 3B exercises the orcid path, and the spec's ambiguity is
        # resolved deliberately in its favor (a name search stays a discovery
        # step; the denial fires when the denied person is looked up directly).
        if orcid is not None and orcid in st.settings.deny_orcids:
            logger.info("denied orcid lookup", extra={"ctx": {"orcid": orcid}})
            raise ApiError(403, "not_authorized", "WDP denies access to this person")
        sql, args = _persons_sql(orcid, name, limit)
        rows = await st.pool.fetch(sql, *args)
        return {
            "results": [
                {
                    "ref_id": str(row["ref_id"]),
                    "orcid": row["orcid"],
                    "name": row["name"],
                    "affiliations": list(row["affiliations"]),
                    "publication_count": int(row["publication_count"]),
                    "funding_count": int(row["funding_count"]),
                }
                for row in rows
            ],
            "total": int(rows[0]["total"]) if rows else 0,
        }

    @app.get("/v1/entities", dependencies=data_deps)
    async def entities(
        request: Request,
        uei: str | None = None,
        name: str | None = None,
        limit: int | None = None,
    ) -> dict:
        st = request.app.state
        sql, args = _entities_sql(uei, name, limit)
        rows = await st.pool.fetch(sql, *args)
        return {
            "results": [
                {
                    "ref_id": str(row["ref_id"]),
                    "uei": row["uei"],
                    "name": row["name"],
                    "country": row["country"],
                    "record_count": int(row["record_count"]),
                }
                for row in rows
            ],
            "total": int(rows[0]["total"]) if rows else 0,
        }

    @app.get("/v1/documents/{ref_id}", dependencies=data_deps)
    async def documents(
        request: Request, ref_id: str, limit: int | None = None
    ) -> dict:
        st = request.app.state
        try:
            ref_uuid = uuid.UUID(ref_id)
        except ValueError:
            raise ApiError(404, "not_found", "unknown ref_id")
        person = await st.pool.fetchrow(
            "SELECT orcid FROM persons WHERE ref_id = $1", ref_uuid
        )
        if person is not None:
            if person["orcid"] in st.settings.deny_orcids:
                logger.info(
                    "denied ref_id lookup", extra={"ctx": {"ref_id": ref_id}}
                )
                raise ApiError(403, "not_authorized", "WDP denies access to this person")
        else:
            entity = await st.pool.fetchrow(
                "SELECT 1 FROM entities WHERE ref_id = $1", ref_uuid
            )
            if entity is None:
                raise ApiError(404, "not_found", "unknown ref_id")
        sql, args = _documents_sql(ref_uuid, limit)
        rows = await st.pool.fetch(sql, *args)
        return {
            "results": [
                {
                    "doc_id": str(row["doc_id"]),
                    "type": row["type"],
                    "title": row["title"],
                    "year": row["year"],
                    "source": row["source"],
                    "detail": _decode_detail(row["detail"]),
                }
                for row in rows
            ],
            "total": int(rows[0]["total"]) if rows else 0,
        }

    return app


app = create_app()
