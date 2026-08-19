"""Audit record per tools/call (invariant: emitted by the MCP server, never
the agent). Canonical fields per CLAUDE.md / mcp-tools spec: user sub,
component, tool, args, result count/size, duration, outcome, timestamp.
Never the token — UserContext does not even carry it."""
import logging
from datetime import datetime, timezone

from .auth import UserContext

_logger = logging.getLogger("mcp.audit")


def emit_audit(
    *,
    user: UserContext,
    tool: str,
    args: dict,
    result_count: int | None,
    result_bytes: int | None,
    duration_ms: float,
    outcome: str,
) -> None:
    _logger.info(
        "audit",
        extra={
            "ctx": {
                "audit": True,
                "sub": user.sub,
                "component": user.component,
                "tool": tool,
                "args": args,
                "result_count": result_count,
                "result_bytes": result_bytes,
                "duration_ms": round(duration_ms, 3),
                "outcome": outcome,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
