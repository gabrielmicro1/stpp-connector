"""MockMCPFactory: role scoping, contract-conformant envelopes, error
triggers, fixture overrides."""
import jsonschema
import pytest

from agent.errors import MCPToolError
from agent.mcp_mock import MockMCPFactory
from shared.types import UserContext

pytestmark = pytest.mark.anyio

FULL = UserContext(sub="analyst-full", name="A", component="DARPA",
                   roles=("rfff_reader", "wdp_reader"))
LOCAL = UserContext(sub="analyst-local", name="B", component="DARPA",
                    roles=("rfff_reader",))

MINIMAL_ARGS = {
    "search_proposals": {},
    "get_proposal": {"proposal_number": "P-2025-0042"},
    "search_personnel": {"name": "Avery"},
    "aggregate_assessments": {"group_by": ["reviewing_component"]},
    "search_wdp_person": {"orcid": "0000-0001-2345-0001"},
    "search_wdp_entity": {"uei": "UEI0001MOCK"},
    "retrieve_wdp_documents": {"ref_id": "wdp-person-x"},
}


@pytest.fixture
def factory(contracts_dir):
    return MockMCPFactory(contracts_dir)


async def test_tools_list_full_user_sees_all(factory, tool_defs):
    async with factory.session(FULL) as session:
        names = {t["name"] for t in await session.tools_list()}
    assert names == {t["name"] for t in tool_defs}


async def test_tools_list_filtered_for_local_analyst(factory, tool_defs):
    async with factory.session(LOCAL) as session:
        listed = await session.tools_list()
    assert {t["x-role"] for t in listed} == {"rfff_reader"}
    assert len(listed) == 4


async def test_every_canned_result_matches_output_schema(factory, tool_defs):
    schemas = {t["name"]: t["outputSchema"] for t in tool_defs}
    async with factory.session(FULL) as session:
        for name, args in MINIMAL_ARGS.items():
            result = await session.tools_call(name, args)
            jsonschema.validate(result, schemas[name])


async def test_call_without_role_is_not_authorized(factory):
    async with factory.session(LOCAL) as session:
        with pytest.raises(MCPToolError) as exc_info:
            await session.tools_call("search_wdp_person", {"orcid": "x"})
    assert exc_info.value.code == "not_authorized"


async def test_denied_orcid_is_not_authorized(contracts_dir):
    factory = MockMCPFactory(contracts_dir, deny_orcids=frozenset({"0000-9999-0000-0001"}))
    async with factory.session(FULL) as session:
        with pytest.raises(MCPToolError) as exc_info:
            await session.tools_call("search_wdp_person", {"orcid": "0000-9999-0000-0001"})
    assert exc_info.value.code == "not_authorized"


async def test_no_records_orcid_returns_empty(factory):
    async with factory.session(FULL) as session:
        result = await session.tools_call(
            "search_wdp_person", {"orcid": "0000-0001-2345-0002"}
        )
    assert result["data"] == []
    assert result["meta"]["total"] == 0


async def test_unknown_ref_id_is_not_found(factory):
    async with factory.session(FULL) as session:
        with pytest.raises(MCPToolError) as exc_info:
            await session.tools_call("retrieve_wdp_documents", {"ref_id": "bogus"})
    assert exc_info.value.code == "not_found"


async def test_missing_required_arg_is_invalid_args(factory):
    async with factory.session(FULL) as session:
        with pytest.raises(MCPToolError) as exc_info:
            await session.tools_call("get_proposal", {})
    assert exc_info.value.code == "invalid_args"


async def test_get_proposal_personnel_carry_bridge_orcids(factory):
    async with factory.session(FULL) as session:
        result = await session.tools_call("get_proposal", {"proposal_number": "P-1"})
    orcids = [p["person_orcid"] for p in result["data"]["personnel"]]
    assert len(orcids) == 2
    assert any(o.endswith("0002") for o in orcids)


async def test_fixture_override_static_and_callable_and_exception(contracts_dir):
    factory = MockMCPFactory(
        contracts_dir,
        fixtures={
            "search_proposals": {"data": [], "meta": {"total": 0, "returned": 0, "truncated": False}},
            "get_proposal": lambda args: {"data": {"proposal_number": args["proposal_number"], "personnel": []},
                                          "meta": {"total": 1, "returned": 1, "truncated": False}},
            "search_wdp_person": MCPToolError("upstream_unavailable", "wdp down"),
        },
    )
    async with factory.session(FULL) as session:
        assert (await session.tools_call("search_proposals", {}))["data"] == []
        got = await session.tools_call("get_proposal", {"proposal_number": "P-7"})
        assert got["data"]["proposal_number"] == "P-7"
        with pytest.raises(MCPToolError) as exc_info:
            await session.tools_call("search_wdp_person", {"orcid": "x"})
        assert exc_info.value.code == "upstream_unavailable"
