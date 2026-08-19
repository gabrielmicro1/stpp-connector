"""Verify criterion 3: every tools/call emits exactly one audit record with
the canonical fields, on success AND on error outcomes."""
from tests.conftest import make_token

CANONICAL_FIELDS = {
    "audit",
    "sub",
    "component",
    "tool",
    "args",
    "result_count",
    "result_bytes",
    "duration_ms",
    "outcome",
    "timestamp",
}


def test_success_call_audited(rpc, fake_pool, audit_records):
    fake_pool.fetch_results.append([])
    body = rpc(
        make_token(), "tools/call", {"name": "search_proposals", "arguments": {}}
    ).json()
    assert body["result"]["meta"]["returned"] == 0
    assert len(audit_records) == 1
    ctx = audit_records[0].ctx
    assert set(ctx) == CANONICAL_FIELDS
    assert ctx["audit"] is True
    assert ctx["sub"] == "analyst-full"
    assert ctx["component"] == "DARPA"
    assert ctx["tool"] == "search_proposals"
    assert ctx["args"] == {}
    assert ctx["result_count"] == 0
    assert ctx["result_bytes"] > 0
    assert ctx["outcome"] == "ok"


def test_error_outcomes_audited(rpc, audit_records):
    rpc(make_token(), "tools/call", {"name": "get_proposal", "arguments": {}})
    rpc(make_token(), "tools/call", {"name": "no_such_tool", "arguments": {}})
    assert [r.ctx["outcome"] for r in audit_records] == ["invalid_args", "invalid_args"]
    assert [r.ctx["result_count"] for r in audit_records] == [None, None]


def test_tools_list_is_not_audited(rpc, audit_records):
    rpc(make_token(), "tools/list")
    assert audit_records == []
