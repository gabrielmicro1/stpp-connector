"""Reference-language semantics per docs/specs/plan-format.md and the
contracts/plan-format.json root description."""
import pytest

from agent.refs import (
    FanPlan,
    Ref,
    RefFanoutError,
    RefSyntaxError,
    StepResult,
    find_refs,
    parse_ref,
    plan_step_calls,
    resolve,
)


def envelope(data):
    return {"data": data, "meta": {"total": 0, "returned": 0, "truncated": False}}


# --- parse_ref ---------------------------------------------------------------

def test_parse_simple_chain():
    ref = parse_ref("$steps[1].data.personnel[*].person_orcid")
    assert ref.step_id == 1
    assert ref.raw == "$steps[1].data.personnel[*].person_orcid"


def test_parse_bare_step():
    assert parse_ref("$steps[2]").step_id == 2


def test_non_ref_strings_return_none():
    assert parse_ref("P-2025-0042") is None
    assert parse_ref("see $steps[1].data for detail") is None  # mid-string = literal
    assert parse_ref("") is None
    assert parse_ref(42) is None


@pytest.mark.parametrize(
    "bad",
    [
        "$steps[]",            # no id
        "$steps[x].a",         # non-integer id
        "$steps[1]..a",        # empty field segment
        "$steps[1",            # unclosed bracket
        "$steps[1].a[",        # unclosed index
        "$steps[1].a[-1]",     # negative index
        "$steps[1]a",          # segment without separator
        "$steps[1].a[1.5]",    # non-integer index
        "$steps[1].",          # trailing dot
    ],
)
def test_malformed_refs_raise(bad):
    with pytest.raises(RefSyntaxError):
        parse_ref(bad)


# --- find_refs ---------------------------------------------------------------

def test_find_refs_walks_nested_containers():
    args = {
        "filters": {"orcid": "$steps[1].data[0].orcid", "fy": "2025"},
        "ids": ["$steps[2].data[*].id", "literal"],
    }
    found = find_refs(args)
    raws = {ref.raw for _, ref in found}
    assert raws == {"$steps[1].data[0].orcid", "$steps[2].data[*].id"}


def test_find_refs_propagates_syntax_errors():
    with pytest.raises(RefSyntaxError):
        find_refs({"a": "$steps[x].bad"})


# --- resolve -----------------------------------------------------------------

def single(value):
    return StepResult(value=value, fanned=False)


def fanned(values):
    return StepResult(value=list(values), fanned=True)


def test_dotted_and_index_access():
    results = {1: single(envelope([{"proposal_number": "P-1"}]))}
    res = resolve(parse_ref("$steps[1].data[0].proposal_number"), results)
    assert res.values == ["P-1"]
    assert res.fanned is False


def test_star_fans_over_list():
    results = {1: single(envelope({"personnel": [{"o": "A"}, {"o": "B"}]}))}
    res = resolve(parse_ref("$steps[1].data.personnel[*].o"), results)
    assert res.values == ["A", "B"]
    assert res.fanned is True


def test_multiple_stars_flatten_to_one_list():
    data = [{"ids": [1, 2]}, {"ids": [3]}]
    results = {1: single(envelope(data))}
    res = resolve(parse_ref("$steps[1].data[*].ids[*]"), results)
    assert res.values == [1, 2, 3]
    assert res.fanned is True


def test_missing_field_yields_zero_values():
    results = {1: single(envelope([{"a": 1}]))}
    assert resolve(parse_ref("$steps[1].data[0].nope"), results).values == []


def test_index_out_of_range_yields_zero_values():
    results = {1: single(envelope([{"a": 1}]))}
    assert resolve(parse_ref("$steps[1].data[5].a"), results).values == []


def test_dotted_access_into_scalar_yields_zero_values():
    results = {1: single(envelope("just a string"))}
    assert resolve(parse_ref("$steps[1].data.field"), results).values == []


def test_star_on_non_list_yields_zero_values():
    results = {1: single(envelope({"k": "v"}))}
    assert resolve(parse_ref("$steps[1].data[*]"), results).values == []


def test_bare_ref_to_normal_step_is_single_value():
    env = envelope([1])
    res = resolve(parse_ref("$steps[1]"), {1: single(env)})
    assert res.values == [env]
    assert res.fanned is False


def test_bare_ref_to_fanned_step_is_ordered_call_list():
    envs = [envelope([1]), envelope([2])]
    res = resolve(parse_ref("$steps[2]"), {2: fanned(envs)})
    assert res.values == envs
    assert res.fanned is True


def test_ref_into_fanned_step_maps_and_flattens():
    envs = [envelope([{"ref_id": "r1"}, {"ref_id": "r2"}]), envelope([{"ref_id": "r3"}])]
    res = resolve(parse_ref("$steps[2].data[*].ref_id"), {2: fanned(envs)})
    assert res.values == ["r1", "r2", "r3"]
    assert res.fanned is True


# --- plan_step_calls ----------------------------------------------------------

def test_no_refs_single_call():
    plan = plan_step_calls({"group_by": "fiscal_year"}, {}, max_fanout=10)
    assert plan == FanPlan(calls=[{"group_by": "fiscal_year"}], truncated_count=0, empty=False, fanned=False)


def test_scalar_substitution():
    results = {1: single(envelope([{"pn": "P-9"}]))}
    args = {"proposal_number": "$steps[1].data[0].pn"}
    plan = plan_step_calls(args, results, max_fanout=10)
    assert plan.calls == [{"proposal_number": "P-9"}]


def test_scalar_zero_values_means_empty_step():
    results = {1: single(envelope([]))}
    plan = plan_step_calls({"pn": "$steps[1].data[0].pn"}, results, max_fanout=10)
    assert plan.empty is True
    assert plan.calls == []


def test_fanout_one_call_per_value_with_scalar_held_constant():
    results = {1: single(envelope({"people": [{"o": "A"}, {"o": "B"}], "fy": "2025"}))}
    args = {"orcid": "$steps[1].data.people[*].o", "fy": "$steps[1].data.fy", "limit": 5}
    plan = plan_step_calls(args, results, max_fanout=10)
    assert plan.calls == [
        {"orcid": "A", "fy": "2025", "limit": 5},
        {"orcid": "B", "fy": "2025", "limit": 5},
    ]


def test_fanout_cap_truncates_and_counts():
    values = [{"o": str(i)} for i in range(15)]
    results = {1: single(envelope(values))}
    plan = plan_step_calls({"orcid": "$steps[1].data[*].o"}, results, max_fanout=10)
    assert len(plan.calls) == 10
    assert plan.truncated_count == 5
    assert plan.fanned is True


def test_fan_of_one_is_still_fanned():
    results = {1: single(envelope([{"o": "A"}]))}
    plan = plan_step_calls({"orcid": "$steps[1].data[*].o"}, results, max_fanout=10)
    assert plan.calls == [{"orcid": "A"}]
    assert plan.fanned is True


def test_fanout_zero_values_means_empty_step():
    results = {1: single(envelope([]))}
    plan = plan_step_calls({"orcid": "$steps[1].data[*].o"}, results, max_fanout=10)
    assert plan.empty is True
    assert plan.calls == []


def test_refs_substituted_in_nested_containers():
    results = {1: single(envelope([{"pn": "P-9"}]))}
    args = {"filters": {"pn": "$steps[1].data[0].pn"}, "keep": ["x", "$steps[1].data[0].pn"]}
    plan = plan_step_calls(args, results, max_fanout=10)
    assert plan.calls == [{"filters": {"pn": "P-9"}, "keep": ["x", "P-9"]}]


def test_dynamic_double_fan_raises():
    envs = [envelope([1])]
    results = {1: fanned(envs), 2: fanned(envs)}
    args = {"a": "$steps[1]", "b": "$steps[2]"}
    with pytest.raises(RefFanoutError):
        plan_step_calls(args, results, max_fanout=10)
