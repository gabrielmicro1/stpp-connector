from tests.conftest import make_token


def _call(rpc, name, arguments):
    return rpc(make_token(), "tools/call", {"name": name, "arguments": arguments}).json()


def test_unknown_tool(rpc):
    body = _call(rpc, "drop_tables", {})
    assert body["result"]["error"]["code"] == "invalid_args"


def test_schema_violation_wrong_type(rpc, fake_pool):
    body = _call(rpc, "search_proposals", {"filters": {"fiscal_year": 2024}})
    assert body["result"]["error"]["code"] == "invalid_args"
    assert fake_pool.calls == []


def test_schema_violation_extra_property(rpc):
    body = _call(rpc, "search_proposals", {"filters": {"star_rating": "5"}})
    assert body["result"]["error"]["code"] == "invalid_args"


def test_missing_required_argument(rpc):
    body = _call(rpc, "get_proposal", {})
    assert body["result"]["error"]["code"] == "invalid_args"
    assert "proposal_number" in body["result"]["error"]["message"]


def test_aggregate_unknown_group_by(rpc):
    body = _call(rpc, "aggregate_assessments", {"group_by": ["favorite_color"]})
    assert body["result"]["error"]["code"] == "invalid_args"
    assert "favorite_color" in body["result"]["error"]["message"]


def test_empty_search_personnel(rpc):
    body = _call(rpc, "search_personnel", {})
    assert body["result"]["error"]["code"] == "invalid_args"


def test_empty_wdp_person_search(rpc, stub_wdp):
    body = _call(rpc, "search_wdp_person", {})
    assert body["result"]["error"]["code"] == "invalid_args"
    assert stub_wdp.calls == []
