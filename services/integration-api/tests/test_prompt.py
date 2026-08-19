"""Prompt assembly: spec section order (plan-format spec assembly items
1-5), untrusted-data delimiting, and the snapshot golden for a fixture
query. Regenerate the golden with REGEN_GOLDEN=1 after intentional template
changes."""
import os
from pathlib import Path

import pytest

from agent.prompt import (
    render_conversation_block,
    Budgets,
    StepReport,
    assemble_plan_prompt,
    assemble_plan_repair_suffix,
    assemble_step_repair_prompt,
    assemble_synthesis_prompt,
)

GOLDEN = Path(__file__).parent / "golden" / "plan_prompt.txt"

FIXTURE_QUERY = (
    "Give me research background on the personnel of proposal P-2025-0042."
)

FIXTURE_PLANNER_CONTEXT = {
    "fields": {
        "fiscal_year": {
            "description": "Fiscal year of the proposal.",
            "requirement": "Required",
            "data_type": "Choice",
            "dictionary_values": ["2024", "2025"],
            "observed_values": [
                {"value": "2024", "row_count": 120, "in_dictionary": True},
                {"value": "2025", "row_count": 216, "in_dictionary": True},
                {"value": "2026", "row_count": 4, "in_dictionary": False},
            ],
        },
        "person_overall_assessment": {
            "description": "Overall person assessment.",
            "requirement": "Optional",
            "data_type": "Calculated",
            "dictionary_values": None,
            "observed_values": [
                {"value": "High", "row_count": 40, "in_dictionary": False},
                {"value": "Low", "row_count": 300, "in_dictionary": False},
            ],
        },
        "review_notes": {
            "description": "Free-text reviewer notes.",
            "requirement": "Optional",
            "data_type": "Text",
            "dictionary_values": None,
            "observed_values": None,
        },
    },
    "caveats": [
        "fiscal_year: observed value(s) not documented in the dictionary: 2026.",
        "person_overall_assessment is an opaque upstream value; report verbatim.",
    ],
    "profile": {"row_count": 340},
}

FIXTURE_CATALOG = [
    {
        "type": "proposal",
        "proposal_number": "P-2025-0042",
        "proposal_title": "Adaptive Sensor Meshes",
        "submitting_entity_name": "Mock University",
        "fiscal_year": "2025",
    },
    {
        "type": "person",
        "person_orcid": "0000-0001-2345-0001",
        "name": "Avery Mockperson",
        "proposal_numbers": ["P-2025-0042"],
    },
]

BUDGETS = Budgets(plan_max_steps=8, plan_max_fanout=10)


@pytest.fixture
def plan_prompt(tool_defs, plan_schema):
    return assemble_plan_prompt(
        query=FIXTURE_QUERY,
        planner_context=FIXTURE_PLANNER_CONTEXT,
        catalog_matches=FIXTURE_CATALOG,
        tools=tool_defs,
        plan_schema=plan_schema,
        budgets=BUDGETS,
    )


def test_sections_in_spec_order(plan_prompt):
    positions = [
        plan_prompt.index("# Role"),
        plan_prompt.index("# RFFF data context"),
        plan_prompt.index("# Catalog entries matched to this query"),
        plan_prompt.index("# Available tools (this session)"),
        plan_prompt.index("# Output format"),
        plan_prompt.index("# Conversation so far"),
        plan_prompt.index("# User query"),
    ]
    assert positions == sorted(positions)


def test_conversation_block_rendering():
    assert render_conversation_block([]) == "(none — first turn)"
    history = [
        {"role": "user", "content": "how many in FY2025?"},
        {"role": "assistant", "content": "A" * 30},
    ]
    block = render_conversation_block(history, replay_chars=10)
    assert "<<<TURN role=user\nhow many in FY2025?\nTURN>>>" in block
    # assistant turns are truncated to replay_chars with a marker
    assert "<<<TURN role=assistant\n" + "A" * 10 + "\n… [truncated]\nTURN>>>" in block
    # user turns are never truncated
    long_user = render_conversation_block(
        [{"role": "user", "content": "B" * 30}], replay_chars=10
    )
    assert "B" * 30 in long_user


def test_plan_prompt_carries_history(planner_inputs, tool_defs, plan_schema):
    from agent.prompt import Budgets

    prompt = assemble_plan_prompt(
        query="compare to FY2024",
        planner_context=planner_inputs.planner_context,
        catalog_matches=planner_inputs.catalog_matches,
        tools=tool_defs,
        plan_schema=plan_schema,
        budgets=Budgets(plan_max_steps=8, plan_max_fanout=10),
        history=[{"role": "user", "content": "how many in FY2025?"},
                 {"role": "assistant", "content": "66 total"}],
    )
    assert "<<<TURN role=user\nhow many in FY2025?\nTURN>>>" in prompt
    assert "<<<TURN role=assistant\n66 total\nTURN>>>" in prompt
    assert prompt.index("# Conversation so far") < prompt.index("# User query")


def test_plan_prompt_without_history_shows_none(plan_prompt):
    assert "(none — first turn)" in plan_prompt


def test_query_is_delimited_as_untrusted(plan_prompt):
    start = plan_prompt.index("<<<USER_QUERY")
    end = plan_prompt.index("USER_QUERY>>>")
    assert start < plan_prompt.index(FIXTURE_QUERY) < end
    assert "UNTRUSTED" in plan_prompt


def test_context_renders_observed_values_with_counts(plan_prompt):
    assert '"2026" (4 rows, undocumented)' in plan_prompt
    assert '"2025" (216 rows)' in plan_prompt


def test_caveats_render_verbatim(plan_prompt):
    for caveat in FIXTURE_PLANNER_CONTEXT["caveats"]:
        assert caveat in plan_prompt


def test_catalog_and_tools_render(plan_prompt):
    assert "P-2025-0042" in plan_prompt
    assert "Avery Mockperson" in plan_prompt
    assert "## search_wdp_person" in plan_prompt
    assert "## aggregate_assessments" in plan_prompt


def test_budgets_render(plan_prompt):
    assert "at most 8 steps" in plan_prompt
    assert "capped at 10" in plan_prompt


def test_empty_catalog_renders_none(tool_defs, plan_schema):
    prompt = assemble_plan_prompt(
        query="q",
        planner_context=FIXTURE_PLANNER_CONTEXT,
        catalog_matches=[],
        tools=tool_defs,
        plan_schema=plan_schema,
        budgets=BUDGETS,
    )
    assert "(no catalog entries matched this query)" in prompt


def test_repair_suffix_appends_violations(plan_prompt):
    suffix = assemble_plan_repair_suffix(["step 1: bad tool", "duplicate ids"])
    assert "step 1: bad tool" in suffix
    assert "duplicate ids" in suffix
    combined = plan_prompt + suffix
    assert combined.startswith(plan_prompt)


def test_step_repair_prompt(tool_defs):
    failed = {
        "id": 3,
        "tool": "retrieve_wdp_documents",
        "args": {"ref_id": "stale"},
        "reason": "detail",
        "depends_on": [2],
    }
    prompt = assemble_step_repair_prompt(
        intent="research personnel background",
        failed_step=failed,
        error_code="not_found",
        error_message="no WDP reference 'stale'",
        tools=tool_defs,
        budgets=BUDGETS,
    )
    assert "research personnel background" in prompt
    assert '"ref_id": "stale"' in prompt
    assert "not_found" in prompt
    assert "same step id" in prompt
    assert "broader search tool" in prompt
    assert "## search_wdp_person" in prompt


def test_synthesis_prompt_reports_gaps():
    reports = [
        StepReport(
            step_id=1, tool="get_proposal", status="complete",
            summary="1 row", rows=1, truncated=False,
            result_json='{"data": {"proposal_number": "P-2025-0042"}}', error=None,
        ),
        StepReport(
            step_id=2, tool="search_wdp_person", status="failed",
            summary=None, rows=None, truncated=None, result_json=None,
            error="not_authorized: WDP denies access",
        ),
    ]
    prompt = assemble_synthesis_prompt(
        intent="background on personnel",
        query=FIXTURE_QUERY,
        step_reports=reports,
        caveats=["person_overall_assessment is opaque."],
        tool_names=["get_proposal", "search_personnel"],
        history=[{"role": "user", "content": "earlier question about P-2025"}],
    )
    assert "<<<TURN role=user\nearlier question about P-2025\nTURN>>>" in prompt
    assert "- search_personnel" in prompt
    assert "Tools available to this user" in prompt
    assert "## Step 1 — get_proposal [complete]" in prompt
    assert "## Step 2 — search_wdp_person [failed]" in prompt
    assert "not_authorized: WDP denies access" in prompt
    assert "person_overall_assessment is opaque." in prompt
    assert "<<<USER_QUERY" in prompt
    assert "denial" in prompt


def test_plan_prompt_snapshot(plan_prompt):
    if os.getenv("REGEN_GOLDEN"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(plan_prompt)
        pytest.skip("golden regenerated")
    assert GOLDEN.is_file(), "golden missing: run with REGEN_GOLDEN=1 to create it"
    assert plan_prompt == GOLDEN.read_text()


def test_tools_render_result_schemas_and_envelope_rule(plan_prompt):
    assert "Result schema (what $steps references resolve against):" in plan_prompt
    assert '"$steps[1].data.personnel[*].person_orcid"' in plan_prompt
