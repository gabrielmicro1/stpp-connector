"""The five plan-validation checks (plan-format spec), against the real
frozen contracts."""
import copy

import pytest

from agent.validator import ToolIndex, validate_plan, validate_step


@pytest.fixture
def tools(tool_defs):
    return ToolIndex.from_tools(tool_defs)


@pytest.fixture
def local_tools(tool_defs):
    return ToolIndex.from_tools([t for t in tool_defs if t["x-role"] == "rfff_reader"])


def query1_plan():
    return {
        "intent": "count FY2025 proposals with factor-4 prohibited factors by component",
        "steps": [
            {
                "id": 1,
                "tool": "aggregate_assessments",
                "args": {
                    "group_by": ["reviewing_component"],
                    "filters": {
                        "fiscal_year": "2025",
                        "factor4_assessment": "Prohibited Factors",
                    },
                },
                "reason": "rollup without hauling rows",
                "depends_on": [],
            }
        ],
    }


def query2_plan():
    return {
        "intent": "research background on the personnel of proposal P-2025-0042",
        "steps": [
            {
                "id": 1,
                "tool": "get_proposal",
                "args": {"proposal_number": "P-2025-0042"},
                "reason": "pull the proposal record and its personnel",
                "depends_on": [],
            },
            {
                "id": 2,
                "tool": "search_wdp_person",
                "args": {"orcid": "$steps[1].data.personnel[*].person_orcid"},
                "reason": "research-world background on each person",
                "depends_on": [1],
            },
            {
                "id": 3,
                "tool": "retrieve_wdp_documents",
                "args": {"ref_id": "$steps[2].data[*].ref_id"},
                "reason": "document detail for each person found",
                "depends_on": [2],
            },
        ],
    }


def check(plan, *, schema, tools, observed_enums, max_steps=8):
    return validate_plan(
        plan, schema=schema, tools=tools, observed_enums=observed_enums, max_steps=max_steps
    )


def test_valid_query1_plan(plan_schema, tools, observed_enums):
    assert check(query1_plan(), schema=plan_schema, tools=tools, observed_enums=observed_enums) == []


def test_valid_query2_plan(plan_schema, tools, observed_enums):
    assert check(query2_plan(), schema=plan_schema, tools=tools, observed_enums=observed_enums) == []


# --- check 1: document schema --------------------------------------------------

def test_not_an_object(plan_schema, tools, observed_enums):
    assert check([], schema=plan_schema, tools=tools, observed_enums=observed_enums)


def test_missing_intent(plan_schema, tools, observed_enums):
    plan = query1_plan()
    del plan["intent"]
    assert any("intent" in v for v in check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums))


def test_empty_steps(plan_schema, tools, observed_enums):
    plan = query1_plan()
    plan["steps"] = []
    assert check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums)


def test_extra_top_level_key(plan_schema, tools, observed_enums):
    plan = query1_plan()
    plan["notes"] = "x"
    assert check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums)


def test_step_missing_reason(plan_schema, tools, observed_enums):
    plan = query1_plan()
    del plan["steps"][0]["reason"]
    assert any("reason" in v for v in check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums))


# --- check 2: tool existence ---------------------------------------------------

def test_unknown_tool(plan_schema, tools, observed_enums):
    plan = query1_plan()
    plan["steps"][0]["tool"] = "drop_all_tables"
    violations = check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums)
    assert any("drop_all_tables" in v for v in violations)


def test_wdp_tool_invisible_to_local_analyst(plan_schema, local_tools, observed_enums):
    violations = check(query2_plan(), schema=plan_schema, tools=local_tools, observed_enums=observed_enums)
    assert any("search_wdp_person" in v for v in violations)
    assert any("retrieve_wdp_documents" in v for v in violations)


# --- check 3: args -------------------------------------------------------------

def test_missing_required_arg(plan_schema, tools, observed_enums):
    plan = query2_plan()
    plan["steps"][0]["args"] = {}
    assert any("proposal_number" in v for v in check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums))


def test_wrong_arg_type(plan_schema, tools, observed_enums):
    plan = query2_plan()
    plan["steps"][0]["args"]["limit"] = "ten"
    assert check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums)


def test_unknown_arg_key(plan_schema, tools, observed_enums):
    plan = query1_plan()
    plan["steps"][0]["args"]["shazam"] = 1
    assert check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums)


def test_defs_ref_resolution_inside_filters(plan_schema, tools, observed_enums):
    """search_proposals.filters is a $ref into inputSchema-local $defs; an
    unknown filter key must be caught through that indirection."""
    plan = query1_plan()
    plan["steps"][0] = {
        "id": 1,
        "tool": "search_proposals",
        "args": {"filters": {"not_a_filter": "x"}},
        "reason": "r",
        "depends_on": [],
    }
    assert check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums)


def test_unobserved_enum_value(plan_schema, tools, observed_enums):
    plan = query1_plan()
    plan["steps"][0]["args"]["filters"]["fiscal_year"] = "1999"
    violations = check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums)
    assert any("fiscal_year" in v and "1999" in v for v in violations)


def test_observed_enum_value_passes(plan_schema, tools, observed_enums):
    plan = query1_plan()
    plan["steps"][0]["args"]["filters"]["fiscal_year"] = "2024"
    assert check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums) == []


def test_ref_placeholder_skips_enum_check(plan_schema, tools, observed_enums):
    plan = query2_plan()
    plan["steps"].append(
        {
            "id": 4,
            "tool": "search_proposals",
            "args": {"filters": {"fiscal_year": "$steps[1].data.fiscal_year"}},
            "reason": "r",
            "depends_on": [1],
        }
    )
    assert check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums) == []


def test_ref_in_integer_slot_fails_structurally(plan_schema, tools, observed_enums):
    plan = query2_plan()
    plan["steps"][1]["args"]["limit"] = "$steps[1].data.limit"
    assert check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums)


# --- check 4: graph / reference consistency ------------------------------------

def test_duplicate_ids(plan_schema, tools, observed_enums):
    plan = query2_plan()
    plan["steps"][1]["id"] = 1
    plan["steps"][1]["args"] = {"orcid": "x"}
    plan["steps"][1]["depends_on"] = []
    violations = check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums)
    assert any("unique" in v or "duplicate" in v for v in violations)


def test_depends_on_nonexistent_step(plan_schema, tools, observed_enums):
    plan = query1_plan()
    plan["steps"][0]["depends_on"] = [9]
    assert check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums)


def test_ref_to_nonexistent_step(plan_schema, tools, observed_enums):
    plan = query2_plan()
    plan["steps"][1]["args"]["orcid"] = "$steps[7].data[0].orcid"
    plan["steps"][1]["depends_on"] = [7]
    assert check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums)


def test_ref_target_missing_from_depends_on(plan_schema, tools, observed_enums):
    plan = query2_plan()
    plan["steps"][1]["depends_on"] = []
    violations = check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums)
    assert any("depends_on" in v for v in violations)


def test_extra_depends_on_without_ref_is_allowed(plan_schema, tools, observed_enums):
    plan = query2_plan()
    plan["steps"][2]["depends_on"] = [1, 2]  # 1 is an ordering hint, no ref
    assert check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums) == []


def test_self_dependency_cycle(plan_schema, tools, observed_enums):
    plan = query1_plan()
    plan["steps"][0]["depends_on"] = [1]
    assert any("cycl" in v for v in check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums))


def test_two_step_cycle(plan_schema, tools, observed_enums):
    plan = query2_plan()
    plan["steps"] = plan["steps"][:2]
    plan["steps"][0]["depends_on"] = [2]
    assert any("cycl" in v for v in check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums))


def test_malformed_ref_is_violation(plan_schema, tools, observed_enums):
    plan = query2_plan()
    plan["steps"][1]["args"]["orcid"] = "$steps[x].data"
    assert check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums)


def test_two_fanout_refs_in_one_step(plan_schema, tools, observed_enums):
    plan = query2_plan()
    plan["steps"][2]["args"] = {
        "ref_id": "$steps[2].data[*].ref_id",
        "limit": 5,
    }
    plan["steps"][2]["args"]["ref_id2"] = "$steps[1].data.personnel[*].person_orcid"
    # ref_id2 is also an unknown key; assert the fan-out violation specifically
    plan["steps"][2]["depends_on"] = [1, 2]
    violations = check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums)
    assert any("fan" in v for v in violations)


# --- check 5: budget -----------------------------------------------------------

def test_too_many_steps_is_validation_failure(plan_schema, tools, observed_enums):
    plan = query1_plan()
    step = plan["steps"][0]
    plan["steps"] = []
    for i in range(1, 10):
        s = copy.deepcopy(step)
        s["id"] = i
        plan["steps"].append(s)
    violations = check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums, max_steps=8)
    assert any("PLAN_MAX_STEPS" in v for v in violations)


def test_all_violations_collected(plan_schema, tools, observed_enums):
    plan = query2_plan()
    plan["steps"][0]["tool"] = "nope"                                # check 2
    plan["steps"][1]["args"]["orcid"] = "$steps[9].data[0].o"        # check 4 (bad target)
    plan["steps"][2]["args"] = {"limit": "ten", "ref_id": "r"}       # check 3
    violations = check(plan, schema=plan_schema, tools=tools, observed_enums=observed_enums)
    assert len(violations) >= 3


# --- validate_step (runtime repair) --------------------------------------------

def test_validate_step_ok(plan_schema, tools, observed_enums):
    step = {
        "id": 2,
        "tool": "search_wdp_person",
        "args": {"name": "Jane Roe"},
        "reason": "fall back to name search",
        "depends_on": [1],
    }
    assert validate_step(
        step, schema=plan_schema, tools=tools, observed_enums=observed_enums, valid_dep_ids={1}
    ) == []


def test_validate_step_rejects_unknown_tool_and_bad_dep(plan_schema, tools, observed_enums):
    step = {
        "id": 2,
        "tool": "nope",
        "args": {},
        "reason": "r",
        "depends_on": [5],
    }
    violations = validate_step(
        step, schema=plan_schema, tools=tools, observed_enums=observed_enums, valid_dep_ids={1}
    )
    assert any("nope" in v for v in violations)
    assert any("5" in v for v in violations)
