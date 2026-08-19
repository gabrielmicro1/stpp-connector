"""HTTPMCPFactory/HTTPMCPSession: JSON-RPC framing, delegated-JWT header,
and error mapping (result.error -> MCPToolError; everything abnormal ->
MCPTransportError)."""
import json

import httpx
import pytest

from agent.errors import MCPToolError, MCPTransportError
from agent.mcp_http import HTTPMCPFactory
from shared.types import UserContext

pytestmark = pytest.mark.anyio

USER = UserContext(
    sub="analyst-full",
    name="Avery Fullaccess",
    component="DARPA",
    roles=("rfff_reader", "wdp_reader"),
    token="delegated-jwt-verbatim",
)


def make_factory(handler) -> tuple[HTTPMCPFactory, list]:
    captured: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    factory = HTTPMCPFactory(
        "http://mcp-server:8001", transport=httpx.MockTransport(recording)
    )
    return factory, captured


def rpc_ok(result: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": result}
        )

    return handler


async def test_authorization_header_is_the_delegated_jwt():
    factory, captured = make_factory(rpc_ok({"tools": []}))
    async with factory.session(USER) as session:
        await session.tools_list()
    assert captured[0].headers["Authorization"] == "Bearer delegated-jwt-verbatim"


async def test_requests_are_json_rpc_2_with_incrementing_ids():
    factory, captured = make_factory(rpc_ok({"data": [], "meta": {}}))
    async with factory.session(USER) as session:
        await session.tools_call("search_proposals", {})
        await session.tools_call("search_proposals", {"keywords": "x"})
    bodies = [json.loads(r.content) for r in captured]
    assert [b["jsonrpc"] for b in bodies] == ["2.0", "2.0"]
    assert bodies[0]["id"] != bodies[1]["id"]
    assert bodies[1]["method"] == "tools/call"
    assert bodies[1]["params"] == {
        "name": "search_proposals",
        "arguments": {"keywords": "x"},
    }


async def test_tools_list_returns_tools_array():
    tools = [{"name": "search_proposals", "x-role": "rfff_reader"}]
    factory, _ = make_factory(rpc_ok({"tools": tools}))
    async with factory.session(USER) as session:
        assert await session.tools_list() == tools


async def test_tools_call_returns_envelope():
    envelope = {"data": [{"proposal_number": "P-1"}], "meta": {"total": 1}}
    factory, _ = make_factory(rpc_ok(envelope))
    async with factory.session(USER) as session:
        assert await session.tools_call("search_proposals", {}) == envelope


@pytest.mark.parametrize(
    "code", ["not_authorized", "not_found", "invalid_args", "upstream_unavailable"]
)
async def test_result_error_raises_tool_error(code):
    factory, _ = make_factory(rpc_ok({"error": {"code": code, "message": "why"}}))
    async with factory.session(USER) as session:
        with pytest.raises(MCPToolError) as exc:
            await session.tools_call("search_wdp_person", {"orcid": "x"})
    assert exc.value.code == code
    assert exc.value.message == "why"


async def test_http_error_status_is_transport_error():
    factory, _ = make_factory(lambda r: httpx.Response(500, text="boom"))
    async with factory.session(USER) as session:
        with pytest.raises(MCPTransportError):
            await session.tools_list()


async def test_401_is_transport_error():
    factory, _ = make_factory(
        lambda r: httpx.Response(401, json={"error": {"code": "unauthorized"}})
    )
    async with factory.session(USER) as session:
        with pytest.raises(MCPTransportError):
            await session.tools_list()


async def test_network_failure_is_transport_error():
    def explode(request):
        raise httpx.ConnectError("refused", request=request)

    factory, _ = make_factory(explode)
    async with factory.session(USER) as session:
        with pytest.raises(MCPTransportError):
            await session.tools_call("search_proposals", {})


async def test_json_rpc_error_object_is_transport_error():
    factory, _ = make_factory(
        lambda r: httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "no"}},
        )
    )
    async with factory.session(USER) as session:
        with pytest.raises(MCPTransportError):
            await session.tools_call("search_proposals", {})


async def test_malformed_body_is_transport_error():
    factory, _ = make_factory(lambda r: httpx.Response(200, content=b"not json"))
    async with factory.session(USER) as session:
        with pytest.raises(MCPTransportError):
            await session.tools_list()
