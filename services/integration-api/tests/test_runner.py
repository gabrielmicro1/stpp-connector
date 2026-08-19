"""The orchestrator: planning + one repair, execution with fan-out and both
repair paths, synthesis-always-runs, and the error-code mapping."""
import json

import pytest

from agent.errors import BudgetExceededError, LLMUnavailableError, MCPToolError
from agent.mcp_mock import MockMCPFactory
from agent.runner import Agent
from shared.types import UserContext
from tests.conftest import FakeContextProvider, ScriptedLLM

pytestmark = pytest.mark.anyio

FULL = UserContext(sub="analyst-full", name="A", component="DARPA",
                   roles=("rfff_reader", "wdp_reader"))


def q1_plan():
    return {
        "intent": "count FY2025 prohibited-factor proposals by component",
        "steps": [
            {
                "id": 1,
                "tool": "aggregate_assessments",
                "args": {
                    "group_by": ["reviewing_component"],
                    "filters": {"fiscal_year": "2025",
                                "factor4_assessment": "Prohibited Factors"},
                },
                "reason": "rollup",
                "depends_on": [],
            }
        ],
    }


def q2_plan():
    return {
        "intent": "research background on the personnel of proposal P-2025-0042",
        "steps": [
            {
                "id": 1,
                "tool": "get_proposal",
                "args": {"proposal_number": "P-2025-0042"},
                "reason": "personnel",
                "depends_on": [],
            },
            {
                "id": 2,
                "tool": "search_wdp_person",
                "args": {"orcid": "$steps[1].data.personnel[*].person_orcid"},
                "reason": "wdp summaries",
                "depends_on": [1],
            },
            {
                "id": 3,
                "tool": "retrieve_wdp_documents",
                "args": {"ref_id": "$steps[2].data[*].ref_id"},
                "reason": "document detail",
                "depends_on": [2],
            },
        ],
    }


def make_agent(responses, *, agent_config, planner_inputs, contracts_dir,
               fixtures=None, deny_orcids=frozenset()):
    llm = ScriptedLLM(responses)
    agent = Agent(
        llm=llm,
        mcp=MockMCPFactory(contracts_dir, fixtures=fixtures, deny_orcids=deny_orcids),
        context=FakeContextProvider(planner_inputs),
        config=agent_config,
    )
    return agent, llm


async def test_history_reaches_plan_and_synthesis_prompts(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    agent, llm = make_agent(
        [json.dumps(q1_plan()), "the answer"],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
    )
    history = [
        {"role": "user", "content": "how many prohibited in FY2025?"},
        {"role": "assistant", "content": "66 across 7 components"},
    ]
    outcome = await agent.run_query(
        "compare to FY2024", FULL, recording_sink, history=history
    )
    assert outcome == "complete"
    plan_prompt, synthesis_prompt = llm.prompts[0], llm.prompts[-1]
    for prompt in (plan_prompt, synthesis_prompt):
        assert "<<<TURN role=user\nhow many prohibited in FY2025?\nTURN>>>" in prompt
        assert "<<<TURN role=assistant\n66 across 7 components\nTURN>>>" in prompt
    # existing single-turn call sites pass no history -> the block renders (none)


async def test_query1_happy_path_event_sequence(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    agent, llm = make_agent(
        [json.dumps(q1_plan()), "the answer"],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
    )
    outcome = await agent.run_query("how many?", FULL, recording_sink)
    assert outcome == "complete"
    assert recording_sink.kinds() == [
        "plan", "status", "step", "step", "status", "answer"
    ]
    assert recording_sink.events[1] == ("status", "executing")
    assert recording_sink.events[4] == ("status", "synthesizing")
    first_step = recording_sink.steps()[0]
    assert first_step[0] == 1 and first_step[1] == "running"
    assert first_step[2]["tool"] == "aggregate_assessments"
    done_step = recording_sink.steps()[1]
    assert done_step[1] == "complete"
    assert done_step[2]["rows"] == 3
    assert recording_sink.events[-1] == ("answer", "the answer")
    assert llm.json_modes == [True, False]


async def test_query2_fanout_stores_ordered_list_and_notes_gap(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    agent, llm = make_agent(
        [json.dumps(q2_plan()), "the answer"],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
    )
    outcome = await agent.run_query("background?", FULL, recording_sink)
    assert outcome == "complete"
    step2_done = next(
        s for s in recording_sink.steps() if s[0] == 2 and s[1] == "complete"
    )
    result = step2_done[2]["result"]
    assert isinstance(result, list) and len(result) == 2  # ordered per-call list
    assert result[0]["data"][0]["ref_id"].startswith("wdp-person-")
    assert result[1]["data"] == []  # the no-WDP-records person
    assert step2_done[2]["rows"] == 1
    step3_done = next(
        s for s in recording_sink.steps() if s[0] == 3 and s[1] == "complete"
    )
    assert step3_done[2]["rows"] == 2  # two documents for the one found person
    # synthesis saw the results
    assert "wdp-person-" in llm.prompts[-1]


async def test_zero_value_reference_completes_empty_with_no_calls(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    calls = []

    def empty_proposal(args):
        return {
            "data": {"proposal_number": args["proposal_number"], "personnel": []},
            "meta": {"total": 1, "returned": 1, "truncated": False},
        }

    def record_wdp(args):
        calls.append(args)
        return {"data": [], "meta": {"total": 0, "returned": 0, "truncated": False}}

    plan = q2_plan()
    plan["steps"] = plan["steps"][:2]
    agent, _ = make_agent(
        [json.dumps(plan), "the answer"],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
        fixtures={"get_proposal": empty_proposal, "search_wdp_person": record_wdp},
    )
    outcome = await agent.run_query("q", FULL, recording_sink)
    assert outcome == "complete"
    assert calls == []  # zero tool calls
    step2_done = next(
        s for s in recording_sink.steps() if s[0] == 2 and s[1] == "complete"
    )
    assert step2_done[2]["rows"] == 0
    assert "0 tool calls" in step2_done[2]["summary"]


async def test_planning_repair_success_appends_violations_to_same_prompt(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    bad = q1_plan()
    bad["steps"][0]["tool"] = "not_a_tool"
    agent, llm = make_agent(
        [json.dumps(bad), json.dumps(q1_plan()), "the answer"],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
    )
    outcome = await agent.run_query("q", FULL, recording_sink)
    assert outcome == "complete"
    assert llm.prompts[1].startswith(llm.prompts[0])
    assert "not_a_tool" in llm.prompts[1]
    plans = [e for e in recording_sink.events if e[0] == "plan"]
    assert len(plans) == 1 and plans[0][1] == q1_plan()


async def test_planning_repair_failure_is_plan_invalid(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    bad = q1_plan()
    bad["steps"][0]["tool"] = "not_a_tool"
    agent, _ = make_agent(
        [json.dumps(bad), json.dumps(bad)],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
    )
    outcome = await agent.run_query("q", FULL, recording_sink)
    assert outcome == "failed"
    assert recording_sink.kinds() == ["error"]  # no plan event, no steps
    assert recording_sink.events[0][1] == "plan_invalid"
    assert "not_a_tool" in recording_sink.events[0][2]


async def test_non_json_then_valid_takes_repair_path(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    agent, llm = make_agent(
        ["I think the plan should be...", json.dumps(q1_plan()), "the answer"],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
    )
    assert await agent.run_query("q", FULL, recording_sink) == "complete"
    assert "not valid JSON" in llm.prompts[1]


async def test_fenced_json_is_accepted_without_repair(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    fenced = "```json\n" + json.dumps(q1_plan()) + "\n```"
    agent, llm = make_agent(
        [fenced, "the answer"],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
    )
    assert await agent.run_query("q", FULL, recording_sink) == "complete"
    assert len(llm.prompts) == 2


async def test_llm_unavailable_is_terminal(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    agent, _ = make_agent(
        [LLMUnavailableError("endpoint down")],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
    )
    outcome = await agent.run_query("q", FULL, recording_sink)
    assert outcome == "failed"
    assert recording_sink.events[0][:2] == ("error", "llm_unavailable")


async def test_max_tokens_on_synthesis_is_budget_exceeded(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    agent, _ = make_agent(
        [json.dumps(q1_plan()), BudgetExceededError("output cap")],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
    )
    outcome = await agent.run_query("q", FULL, recording_sink)
    assert outcome == "failed"
    assert recording_sink.events[-1][:2] == ("error", "budget_exceeded")
    # the plan executed before synthesis failed
    assert "plan" in recording_sink.kinds()


async def test_runtime_repair_success(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    plan = {
        "intent": "pull one proposal",
        "steps": [
            {"id": 1, "tool": "get_proposal",
             "args": {"proposal_number": "P-GONE"},
             "reason": "direct", "depends_on": []},
        ],
    }
    repaired = {
        "id": 1, "tool": "search_proposals",
        "args": {"keywords": "P-GONE"},
        "reason": "fallback to broader search", "depends_on": [],
    }
    agent, llm = make_agent(
        [json.dumps(plan), json.dumps(repaired), "the answer"],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
        fixtures={"get_proposal": MCPToolError("not_found", "no such proposal")},
    )
    outcome = await agent.run_query("q", FULL, recording_sink)
    assert outcome == "complete"
    statuses = [(s[0], s[1]) for s in recording_sink.steps()]
    assert statuses == [(1, "running"), (1, "repairing"), (1, "complete")]
    repairing = recording_sink.steps()[1][2]
    assert repairing["tool"] == "get_proposal"  # DB sink needs tool on every write
    done = recording_sink.steps()[-1][2]
    assert done["tool"] == "search_proposals"
    repair_prompt = llm.prompts[1]
    assert "not_found" in repair_prompt
    assert "P-GONE" in repair_prompt
    assert "pull one proposal" in repair_prompt


async def test_runtime_repair_wrong_id_fails_step_without_retry(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    plan = {
        "intent": "pull one proposal",
        "steps": [
            {"id": 1, "tool": "get_proposal",
             "args": {"proposal_number": "P-GONE"},
             "reason": "direct", "depends_on": []},
        ],
    }
    repaired = {"id": 9, "tool": "search_proposals", "args": {},
                "reason": "r", "depends_on": []}
    calls = {"n": 0}

    def failing(args):
        calls["n"] += 1
        return MCPToolError("not_found", "gone")

    agent, _ = make_agent(
        [json.dumps(plan), json.dumps(repaired), "the answer"],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
        fixtures={"get_proposal": failing},
    )
    outcome = await agent.run_query("q", FULL, recording_sink)
    assert outcome == "complete"  # step failure is never terminal
    statuses = [(s[0], s[1]) for s in recording_sink.steps()]
    assert statuses == [(1, "running"), (1, "repairing"), (1, "failed")]
    assert calls["n"] == 1  # no retry with an invalid repaired step


async def test_repair_retry_fails_dependents_skipped_siblings_run(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    plan = {
        "intent": "wdp background plus local rollup",
        "steps": [
            {"id": 1, "tool": "search_wdp_person", "args": {"orcid": "0000-0001-1111-9999"},
             "reason": "summary", "depends_on": []},
            {"id": 2, "tool": "retrieve_wdp_documents",
             "args": {"ref_id": "$steps[1].data[*].ref_id"},
             "reason": "detail", "depends_on": [1]},
            {"id": 3, "tool": "search_proposals", "args": {"keywords": "sensor"},
             "reason": "independent local search", "depends_on": []},
        ],
    }
    repaired = {"id": 1, "tool": "search_wdp_person", "args": {"name": "someone"},
                "reason": "retry by name", "depends_on": []}
    agent, _ = make_agent(
        [json.dumps(plan), json.dumps(repaired), "the answer"],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
        fixtures={"search_wdp_person": MCPToolError("upstream_unavailable", "wdp down")},
    )
    outcome = await agent.run_query("q", FULL, recording_sink)
    assert outcome == "complete"
    by_step = {}
    for sid, status, kw in recording_sink.steps():
        by_step.setdefault(sid, []).append((status, kw))
    assert [s for s, _ in by_step[1]] == ["running", "repairing", "failed"]
    assert [s for s, _ in by_step[3]] == ["running", "complete"]
    assert [s for s, _ in by_step[2]] == ["failed"]
    assert "dependency step 1 failed" in by_step[2][0][1]["error"]
    assert recording_sink.events[-1][0] == "answer"


async def test_not_authorized_never_repairs(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    plan = {
        "intent": "wdp background on a denied person",
        "steps": [
            {"id": 1, "tool": "search_wdp_person",
             "args": {"orcid": "0000-9999-0000-0001"},
             "reason": "summary", "depends_on": []},
        ],
    }
    agent, llm = make_agent(
        [json.dumps(plan), "the answer"],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
        deny_orcids=frozenset({"0000-9999-0000-0001"}),
    )
    outcome = await agent.run_query("q", FULL, recording_sink)
    assert outcome == "complete"
    statuses = [(s[0], s[1]) for s in recording_sink.steps()]
    assert statuses == [(1, "running"), (1, "failed")]  # no repairing event
    assert len(llm.prompts) == 2  # plan + synthesis only: no repair prompt
    assert "not_authorized" in llm.prompts[-1]  # synthesis sees the denial


async def test_all_steps_failed_still_synthesizes(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    plan = q1_plan()
    agent, _ = make_agent(
        [json.dumps(plan), json.dumps(plan["steps"][0]), "honest gap answer"],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
        fixtures={"aggregate_assessments": MCPToolError("upstream_unavailable", "db down")},
    )
    outcome = await agent.run_query("q", FULL, recording_sink)
    assert outcome == "complete"
    assert recording_sink.events[-1] == ("answer", "honest gap answer")


async def test_oversized_result_stored_capped(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    big = {
        "data": [{"blob": "x" * 200_000}],
        "meta": {"total": 1, "returned": 1, "truncated": False},
    }
    agent, _ = make_agent(
        [json.dumps(q1_plan()), "the answer"],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
        fixtures={"aggregate_assessments": big},
    )
    outcome = await agent.run_query("q", FULL, recording_sink)
    assert outcome == "complete"
    done = next(s for s in recording_sink.steps() if s[1] == "complete")
    stored = done[2]["result"]
    assert stored["data"] is None
    assert stored["meta"]["stored_truncated"] is True
    assert "stored result truncated" in done[2]["summary"]


async def test_fanout_partial_failure_completes_with_error_slots(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    def wdp(args):
        if args["orcid"].endswith("0002"):
            return MCPToolError("not_found", "no record")
        return {
            "data": [{"ref_id": f"wdp-person-{args['orcid']}", "orcid": args["orcid"],
                      "name": "A", "affiliations": [], "publication_count": 1,
                      "funding_count": 0}],
            "meta": {"total": 1, "returned": 1, "truncated": False},
        }

    plan = q2_plan()
    plan["steps"] = plan["steps"][:2]
    agent, llm = make_agent(
        [json.dumps(plan), "the answer"],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
        fixtures={"search_wdp_person": wdp},
    )
    outcome = await agent.run_query("q", FULL, recording_sink)
    assert outcome == "complete"
    done = next(s for s in recording_sink.steps() if s[0] == 2 and s[1] == "complete")
    assert "1 of 2 calls failed" in done[2]["summary"]
    result = done[2]["result"]
    assert len(result) == 2
    assert "error" in result[1]
    assert len(llm.prompts) == 2  # partial failure does not trigger repair


async def test_fanout_truncation_noted_in_summary(
    agent_config, planner_inputs, contracts_dir, recording_sink
):
    people = [
        {"person_orcid": f"0000-0001-0000-{i:04d}", "first_name": "P",
         "middle_name": None, "last_name": str(i), "proposal_role": "KP",
         "affiliation_name": "U", "factor1_assessment": None,
         "factor2_assessment": None, "factor3_assessment": None,
         "factor4_assessment": None, "person_overall_assessment": None}
        for i in range(1, 16)  # none end in 0002 -> all get summaries
    ]

    def proposal(args):
        return {
            "data": {"proposal_number": args["proposal_number"], "personnel": people},
            "meta": {"total": 1, "returned": 1, "truncated": False},
        }

    plan = q2_plan()
    plan["steps"] = plan["steps"][:2]
    agent, _ = make_agent(
        [json.dumps(plan), "the answer"],
        agent_config=agent_config, planner_inputs=planner_inputs,
        contracts_dir=contracts_dir,
        fixtures={"get_proposal": proposal},
    )
    outcome = await agent.run_query("q", FULL, recording_sink)
    assert outcome == "complete"
    done = next(s for s in recording_sink.steps() if s[0] == 2 and s[1] == "complete")
    assert len(done[2]["result"]) == 10  # PLAN_MAX_FANOUT
    assert "5" in done[2]["summary"] and "truncated" in done[2]["summary"].lower()
