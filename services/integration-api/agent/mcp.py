"""The agent-side MCP client interface (invariants 1, 5).

Phase 6 wires MockMCPFactory behind it; phase 7 implements the real HTTP
client against the MCP server container without changing this interface.
Sessions are created per-request with the delegated user context bound —
the original JWT (user.token) becomes the session's Authorization header in
the HTTP implementation; the MCP server validates it independently.
"""
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from shared.types import UserContext


class MCPSession(Protocol):
    async def tools_list(self) -> list:
        """Full tool definitions (name, description, inputSchema, ...),
        already filtered to what THIS user may call."""
        ...

    async def tools_call(self, tool: str, args: dict) -> dict:
        """Invoke a tool; returns the {data, meta} envelope. Raises
        MCPToolError for structured tool errors and MCPTransportError when
        the session itself fails."""
        ...


class MCPSessionFactory(Protocol):
    def session(self, user: UserContext) -> AbstractAsyncContextManager[MCPSession]: ...
