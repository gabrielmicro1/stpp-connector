"""Family 2 — WDP tools via WDPClient (role: wdp_reader).

Maps fake-wdp's raw {results, total} shape to the MCP {data, meta} envelope
HERE, not in WDPClient, so only the client changes when the real WDP Query
Interface spec lands. WDP-side denials (403) pass through as not_authorized —
distinct from MCP-side scoping and from absence (empty results).
"""
from .errors import ToolError
from .hygiene import envelope
from .wdpclient import WDPClient, WDPError


def _clamp_limit(limit, max_rows: int) -> int:
    if limit is None:
        return max_rows
    return min(int(limit), max_rows)


def _envelope_from(raw: dict, settings) -> dict:
    rows = raw.get("results", [])
    total = raw.get("total", len(rows))
    return envelope(
        rows, total, max_rows=settings.mcp_max_rows, max_chars=settings.mcp_max_text_chars
    )


async def search_wdp_person(wdp: WDPClient, args: dict, settings) -> dict:
    if not (args.get("orcid") or args.get("name")):
        raise ToolError("invalid_args", "provide at least one of orcid or name")
    try:
        raw = await wdp.persons(
            orcid=args.get("orcid"),
            name=args.get("name"),
            limit=_clamp_limit(args.get("limit"), settings.mcp_max_rows),
        )
    except WDPError as exc:
        raise ToolError(exc.code, exc.message) from exc
    return _envelope_from(raw, settings)


async def search_wdp_entity(wdp: WDPClient, args: dict, settings) -> dict:
    if not (args.get("uei") or args.get("name")):
        raise ToolError("invalid_args", "provide at least one of uei or name")
    try:
        raw = await wdp.entities(
            uei=args.get("uei"),
            name=args.get("name"),
            limit=_clamp_limit(args.get("limit"), settings.mcp_max_rows),
        )
    except WDPError as exc:
        raise ToolError(exc.code, exc.message) from exc
    return _envelope_from(raw, settings)


async def retrieve_wdp_documents(wdp: WDPClient, args: dict, settings) -> dict:
    try:
        raw = await wdp.documents(
            args["ref_id"], limit=_clamp_limit(args.get("limit"), settings.mcp_max_rows)
        )
    except WDPError as exc:
        raise ToolError(exc.code, exc.message) from exc
    return _envelope_from(raw, settings)
