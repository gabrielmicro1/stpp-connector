"""MCP server: JSON-RPC 2.0 over a single POST /mcp (methods tools/list and
tools/call), plus GET /healthz.

Separate container by design (invariant 2). tools/list is filtered per user;
every tools/call re-validates the JWT (via require_user), re-checks the tool
role, and emits an audit record — the server never trusts the plan
(invariant 6). Structured tool errors ride inside the JSON-RPC result as
{error: {code, message}}; the JSON-RPC error object is reserved for protocol
failures.
"""
import json
import logging
import time
from contextlib import asynccontextmanager

import jsonschema
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from . import rfff_tools, wdp_tools
from .audit import emit_audit
from .auth import UnauthorizedError, UserContext, require_user, unauthorized_handler
from .config import Settings, load_settings
from .enums import ObservedEnumCache
from .errors import ToolError
from .logging_setup import setup_json_logging
from .registry import ToolRegistry
from .wdpclient import WDPClient

logger = logging.getLogger("mcp.server")

HANDLERS = {
    "search_proposals": rfff_tools.search_proposals,
    "get_proposal": rfff_tools.get_proposal,
    "search_personnel": rfff_tools.search_personnel,
    "aggregate_assessments": rfff_tools.aggregate_assessments,
    "search_wdp_person": wdp_tools.search_wdp_person,
    "search_wdp_entity": wdp_tools.search_wdp_entity,
    "retrieve_wdp_documents": wdp_tools.retrieve_wdp_documents,
}


async def _default_pool(dsn: str):
    import asyncpg

    return await asyncpg.create_pool(dsn)


def create_app(
    *,
    settings: Settings | None = None,
    pool_factory=None,
    wdp_client: WDPClient | None = None,
    enum_cache=None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_json_logging()
        s = settings or load_settings()
        app.state.settings = s
        factory = pool_factory or _default_pool
        app.state.pool = await factory(s.rfff_seed_database_url)
        app.state.wdp = wdp_client or WDPClient(
            base_url=s.wdp_base_url,
            auth_token=s.wdp_auth_token,
            timeout=s.wdp_timeout_seconds,
        )
        app.state.registry = ToolRegistry(s.contracts_dir)
        app.state.enums = enum_cache or ObservedEnumCache(
            lambda: app.state.pool, s.enum_ttl_seconds
        )
        try:
            yield
        finally:
            close = getattr(app.state.pool, "close", None)
            if close is not None:
                await close()

    app = FastAPI(title="PSP7 MCP Server", lifespan=lifespan)
    app.add_exception_handler(UnauthorizedError, unauthorized_handler)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/mcp")
    async def mcp_endpoint(
        request: Request, user: UserContext = Depends(require_user)
    ) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return _rpc_error(None, -32700, "parse error")
        if (
            not isinstance(body, dict)
            or body.get("jsonrpc") != "2.0"
            or "method" not in body
            or "id" not in body
        ):
            rpc_id = body.get("id") if isinstance(body, dict) else None
            return _rpc_error(rpc_id, -32600, "invalid JSON-RPC 2.0 request")
        rpc_id = body["id"]
        params = body.get("params") or {}
        if not isinstance(params, dict):
            return _rpc_error(rpc_id, -32600, "params must be an object")
        state = request.app.state

        if body["method"] == "tools/list":
            observed = await state.enums.get()
            tools = state.registry.visible(
                state.registry.render(observed), user.roles
            )
            return _rpc_result(rpc_id, {"tools": tools})
        if body["method"] == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str) or not isinstance(arguments, dict):
                return _rpc_error(
                    rpc_id, -32600, "tools/call needs a string name and object arguments"
                )
            result = await _dispatch_tool_call(state, user, name, arguments)
            return _rpc_result(rpc_id, result)
        return _rpc_error(rpc_id, -32601, f"unknown method {body['method']!r}")

    return app


async def _dispatch_tool_call(
    state, user: UserContext, name: str, arguments: dict
) -> dict:
    started = time.monotonic()
    outcome = "ok"
    try:
        tool = state.registry.get(name)
        if tool is None:
            raise ToolError("invalid_args", f"unknown tool {name!r}")
        if tool.get("x-role") not in user.roles:
            # The forged-plan gate: authorization is re-checked on EVERY call
            # even though the agent validates plans first (invariant 6).
            raise ToolError(
                "not_authorized",
                f"role {tool.get('x-role')!r} required to call {name}",
            )
        _validate_arguments(tool["inputSchema"], arguments)
        handler = HANDLERS[name]
        if tool.get("x-family") == "wdp":
            result = await handler(state.wdp, arguments, state.settings)
        else:
            result = await handler(state.pool, arguments, state.settings)
    except ToolError as exc:
        outcome = exc.code
        result = {"error": {"code": exc.code, "message": exc.message}}
    except Exception:
        # Never a raw 500 for a tool call: the agent gets a structured,
        # actionable error; the traceback stays server-side.
        logger.exception("tool execution failed", extra={"ctx": {"tool": name}})
        outcome = "upstream_unavailable"
        result = {"error": {"code": "upstream_unavailable", "message": "internal error"}}
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else None
    emit_audit(
        user=user,
        tool=name,
        args=arguments,
        result_count=meta.get("returned") if meta else None,
        result_bytes=len(json.dumps(result, default=str)),
        duration_ms=(time.monotonic() - started) * 1000,
        outcome=outcome,
    )
    return result


def _validate_arguments(input_schema: dict, arguments: dict) -> None:
    validator = jsonschema.Draft202012Validator(input_schema)
    errors = sorted(validator.iter_errors(arguments), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(
            ("/".join(str(p) for p in e.path) + ": " if e.path else "") + e.message
            for e in errors
        )
        raise ToolError("invalid_args", detail)


def _rpc_result(rpc_id, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


def _rpc_error(rpc_id, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}
    )


app = create_app()
