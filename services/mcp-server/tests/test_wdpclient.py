import httpx
import pytest

from app.wdpclient import WDPClient, WDPError

pytestmark = pytest.mark.anyio


def make_client(handler) -> tuple[WDPClient, list]:
    captured: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    client = WDPClient(
        base_url="http://wdp.test",
        auth_token="secret-token",
        transport=httpx.MockTransport(recording),
    )
    return client, captured


async def test_sends_bearer_and_params():
    client, captured = make_client(
        lambda r: httpx.Response(200, json={"results": [], "total": 0})
    )
    raw = await client.persons(orcid="0000-0001-0000-0001", limit=5)
    assert raw == {"results": [], "total": 0}
    request = captured[0]
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert request.url.path == "/v1/persons"
    assert request.url.params["orcid"] == "0000-0001-0000-0001"
    assert request.url.params["limit"] == "5"
    assert "name" not in request.url.params  # None params dropped


async def test_403_maps_to_not_authorized_with_upstream_message():
    client, _ = make_client(
        lambda r: httpx.Response(
            403, json={"error": {"code": "not_authorized", "message": "WDP denies"}}
        )
    )
    with pytest.raises(WDPError) as exc:
        await client.persons(orcid="x")
    assert exc.value.code == "not_authorized"
    assert exc.value.message == "WDP denies"


async def test_404_maps_to_not_found():
    client, _ = make_client(
        lambda r: httpx.Response(404, json={"error": {"code": "not_found"}})
    )
    with pytest.raises(WDPError) as exc:
        await client.documents("wdp-nope")
    assert exc.value.code == "not_found"


async def test_5xx_maps_to_upstream_unavailable():
    client, _ = make_client(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(WDPError) as exc:
        await client.entities(uei="U1")
    assert exc.value.code == "upstream_unavailable"


async def test_network_error_maps_to_upstream_unavailable():
    def explode(request):
        raise httpx.ConnectError("refused", request=request)

    client, _ = make_client(explode)
    with pytest.raises(WDPError) as exc:
        await client.persons(name="ada")
    assert exc.value.code == "upstream_unavailable"


async def test_malformed_json_maps_to_upstream_unavailable():
    client, _ = make_client(lambda r: httpx.Response(200, content=b"not json"))
    with pytest.raises(WDPError) as exc:
        await client.persons(name="ada")
    assert exc.value.code == "upstream_unavailable"
