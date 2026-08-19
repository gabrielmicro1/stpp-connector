"""Thin WDP HTTP client — the ONLY path to WDP (invariants 1, 4).

Speaks HTTP to fake-wdp in the demo and to the real WDP Query Interface in
prod; nothing above it changes. Returns fake-wdp's raw {results, total}
shape — mapping to the MCP {data, meta} envelope happens in the tool layer,
so only this class changes when the real WDP spec lands.
"""
import httpx


class WDPError(Exception):
    """code: not_found | not_authorized | upstream_unavailable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class WDPClient:
    def __init__(
        self,
        *,
        base_url: str,
        auth_token: str,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._auth_token = auth_token
        self._timeout = timeout
        self._transport = transport

    async def persons(self, *, orcid=None, name=None, limit=None) -> dict:
        return await self._get("/v1/persons", {"orcid": orcid, "name": name, "limit": limit})

    async def entities(self, *, uei=None, name=None, limit=None) -> dict:
        return await self._get("/v1/entities", {"uei": uei, "name": name, "limit": limit})

    async def documents(self, ref_id: str, *, limit=None) -> dict:
        return await self._get(f"/v1/documents/{ref_id}", {"limit": limit})

    async def _get(self, path: str, params: dict) -> dict:
        query = {k: v for k, v in params.items() if v is not None}
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                headers={"Authorization": f"Bearer {self._auth_token}"},
            ) as client:
                resp = await client.get(path, params=query)
        except httpx.HTTPError as exc:
            raise WDPError("upstream_unavailable", f"WDP request failed: {exc}") from exc
        if resp.status_code in (403, 404):
            code = "not_authorized" if resp.status_code == 403 else "not_found"
            raise WDPError(code, _upstream_message(resp, code))
        if resp.status_code != 200:
            raise WDPError("upstream_unavailable", f"WDP returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise WDPError("upstream_unavailable", "WDP returned malformed JSON") from exc


def _upstream_message(resp: httpx.Response, fallback: str) -> str:
    try:
        return resp.json()["error"]["message"]
    except Exception:
        return fallback
