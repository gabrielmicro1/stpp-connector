"""Types shared between the API layer and the agent module.

The agent may import from here and NOTHING else under app/ (CLAUDE.md
invariant 3): this module is the entire shared surface.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class UserContext:
    """Delegated user context from the validated JWT, threaded through
    every layer (invariant 5)."""

    sub: str
    name: str
    component: str
    roles: tuple[str, ...]
    # The original delegated JWT, forwarded verbatim as the MCP session's
    # Authorization header (mcp-tools spec); excluded from repr so it never
    # reaches logs.
    token: str = field(default="", repr=False)
