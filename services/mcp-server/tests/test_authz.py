"""Verify criterion 2: a forged tools/call to a WDP tool by a user without
wdp_reader returns not_authorized — the server never trusts the plan."""
from tests.conftest import LOCAL_ROLES, make_token


def test_forged_wdp_call_as_analyst_local_is_not_authorized(rpc, stub_wdp, audit_records):
    token = make_token(sub="analyst-local", roles=list(LOCAL_ROLES))
    body = rpc(
        token, "tools/call", {"name": "search_wdp_person", "arguments": {"orcid": "0000-0001-0000-0001"}}
    ).json()
    assert body["result"]["error"]["code"] == "not_authorized"
    # WDP was never touched.
    assert stub_wdp.calls == []
    # And the denial was audited.
    assert len(audit_records) == 1
    ctx = audit_records[0].ctx
    assert ctx["outcome"] == "not_authorized"
    assert ctx["sub"] == "analyst-local"
    assert ctx["tool"] == "search_wdp_person"


def test_no_roles_cannot_call_local_tools_either(rpc, fake_pool):
    token = make_token(sub="nobody", roles=[])
    body = rpc(
        token, "tools/call", {"name": "search_proposals", "arguments": {}}
    ).json()
    assert body["result"]["error"]["code"] == "not_authorized"
    assert fake_pool.calls == []


def test_authorized_wdp_call_goes_through(rpc, stub_wdp):
    stub_wdp.results.append({"results": [], "total": 0})
    body = rpc(
        make_token(),
        "tools/call",
        {"name": "search_wdp_person", "arguments": {"orcid": "0000-0001-0000-0001"}},
    ).json()
    assert body["result"] == {
        "data": [],
        "meta": {"total": 0, "returned": 0, "truncated": False},
    }
