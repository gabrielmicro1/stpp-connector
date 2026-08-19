from fastapi.testclient import TestClient

from app.main import create_app

from tests.conftest import LOCAL_ROLES, FixedEnums, make_token

ALL_TOOLS = {
    "aggregate_assessments",
    "get_proposal",
    "retrieve_wdp_documents",
    "search_personnel",
    "search_proposals",
    "search_wdp_entity",
    "search_wdp_person",
}
LOCAL_TOOLS = {
    "aggregate_assessments",
    "get_proposal",
    "search_personnel",
    "search_proposals",
}


def _tools(rpc, token):
    body = rpc(token, "tools/list").json()
    return body["result"]["tools"]


def test_analyst_full_sees_all_seven(rpc):
    tools = _tools(rpc, make_token())
    assert {t["name"] for t in tools} == ALL_TOOLS


def test_analyst_local_sees_only_family_1(rpc):
    tools = _tools(rpc, make_token(sub="analyst-local", roles=list(LOCAL_ROLES)))
    assert {t["name"] for t in tools} == LOCAL_TOOLS
    assert all(t["x-role"] == "rfff_reader" for t in tools)


def test_observed_enums_rendered_into_descriptions(rpc):
    tools = {t["name"]: t for t in _tools(rpc, make_token())}
    prop = tools["search_proposals"]["inputSchema"]["$defs"]["filters"]["properties"]
    assert "Observed values: '2023' | '2024'." in prop["fiscal_year"]["description"]
    assert (
        "Observed values: 'Complete' | 'Implemented'."
        in prop["assessment_state"]["description"]
    )
    # x-enum-source must survive rendering: the agent's plan validator
    # reads it straight off tools/list.
    assert prop["fiscal_year"]["x-enum-source"] == "observed_enums"


def test_render_never_mutates_frozen_contracts(rpc, app):
    _tools(rpc, make_token())
    tools = {t["name"]: t for t in _tools(rpc, make_token())}
    desc = tools["search_proposals"]["inputSchema"]["$defs"]["filters"]["properties"][
        "fiscal_year"
    ]["description"]
    # A second render must not double-append.
    assert desc.count("Observed values:") == 1


def test_empty_enum_cache_still_serves_tools(settings, fake_pool, stub_wdp):
    async def pool_factory(dsn):
        return fake_pool

    app = create_app(
        settings=settings,
        pool_factory=pool_factory,
        wdp_client=stub_wdp,
        enum_cache=FixedEnums({}),
    )
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Authorization": f"Bearer {make_token()}"},
        )
    assert {t["name"] for t in resp.json()["result"]["tools"]} == ALL_TOOLS
