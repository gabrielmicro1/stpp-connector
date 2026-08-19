"""HTTP MCP client — the real implementation behind the MCPSession protocol
(invariants 1, 5).

Sessions are per-request: each job run opens its own httpx client with the
delegated user's ORIGINAL JWT as the Authorization header; the MCP server
validates it independently and binds the claims itself. Wire protocol is
minimal JSON-RPC 2.0 over POST /mcp (methods tools/list and tools/call).
Structured tool errors ride inside the JSON-RPC result as {error: {code,
message}} -> MCPToolError; protocol-level failures (JSON-RPC error object,
non-2xx, network, malformed body) -> MCPTransportError.
"""
from contextlib import asynccontextmanager
from itertools import count

import httpx

from shared.types import UserContext

from .errors import MCPToolError, MCPTransportError


class HTTPMCPSession:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._ids = count(1)

    async def tools_list(self) -> list:
        result = await self._rpc("tools/list", {})
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise MCPTransportError("tools/list result missing tools array")
        return tools

    async def tools_call(self, tool: str, args: dict) -> dict:
        result = await self._rpc("tools/call", {"name": tool, "arguments": args})
        error = result.get("error")
        if isinstance(error, dict):
            raise MCPToolError(
                error.get("code", "upstream_unavailable"), error.get("message", "")
            )
        return result

    async def _rpc(self, method: str, params: dict) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": method,
            "params": params,
        }
        try:
            resp = await self._client.post("/mcp", json=payload)
        except httpx.HTTPError as exc:
            raise MCPTransportError(f"MCP request failed: {exc}") from exc
        if resp.status_code != 200:
            raise MCPTransportError(f"MCP server returned HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise MCPTransportError("MCP server returned malformed JSON") from exc
        if not isinstance(body, dict):
            raise MCPTransportError("MCP response is not a JSON-RPC object")
        if "error" in body:
            error = body["error"] or {}
            raise MCPTransportError(
                f"JSON-RPC error {error.get('code')}: {error.get('message')}"
            )
        result = body.get("result")
        if not isinstance(result, dict):
            raise MCPTransportError("JSON-RPC response missing result")
        return result


class HTTPMCPFactory:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._transport = transport

    @asynccontextmanager
    async def session(self, user: UserContext):
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
            headers={"Authorization": f"Bearer {user.token}"},
        ) as client:
            yield HTTPMCPSession(client)
