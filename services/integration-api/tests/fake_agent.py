"""Scripted fake agent for API-layer tests (the phase-4 stub, relocated).

Implements the frozen run_query contract with a canned plan and
AGENT_STUB_STEP_SECONDS-paced progress, so the SSE/lifecycle/budget tests
exercise the API layer without an LLM. Injected via create_app(run_query=...).
"""
import asyncio
import os

from agent.interface import EventSink, Outcome
from shared.types import UserContext


def _canned_plan(query: str) -> dict:
    # Conforms to contracts/plan-format.json; uses real tool names from
    # contracts/mcp-tools/ so the frontend (phase 5) renders realistically.
    return {
        "intent": f"[stub] Retrieve RFFF records relevant to: {query}",
        "steps": [
            {
                "id": 1,
                "tool": "search_proposals",
                "args": {"filters": {"fiscal_year": "2024"}},
                "reason": "Find candidate proposals matching the query (canned).",
                "depends_on": [],
            },
            {
                "id": 2,
                "tool": "get_proposal",
                "args": {"proposal_number": "$steps[1].data[0].proposal_number"},
                "reason": "Fetch the full record for the top match (canned).",
                "depends_on": [1],
            },
        ],
    }


async def run_query(
    query: str, user: UserContext, sink: EventSink, *, history: list[dict] = ()
) -> Outcome:  # history unused by the stub
    delay = float(os.getenv("AGENT_STUB_STEP_SECONDS", "0.8"))
    await asyncio.sleep(delay)  # pretend to plan
    plan = _canned_plan(query)
    await sink.plan(plan)
    await sink.status("executing")
    fake_results = {
        1: {"data": [{"proposal_number": "P-2025-0042"}], "meta": {"returned": 1, "truncated": False}},
        2: {"data": {"proposal_number": "P-2025-0042", "personnel": []}, "meta": {"returned": 1, "truncated": False}},
    }
    for step in plan["steps"]:
        await sink.step(step["id"], "running", tool=step["tool"], args=step["args"])
        await asyncio.sleep(delay)  # pretend to call the MCP server
        result = fake_results[step["id"]]
        await sink.step(
            step["id"],
            "complete",
            tool=step["tool"],
            args=step["args"],
            result=result,
            summary=f"[stub] {step['tool']} returned {result['meta']['returned']} row(s)",
            rows=result["meta"]["returned"],
            truncated=result["meta"]["truncated"],
        )
    await sink.status("synthesizing")
    await asyncio.sleep(delay)
    await sink.answer(
        f"[fake] Canned answer. Your query was: {query!r} (run as "
        f"{user.sub}, roles={list(user.roles)})."
    )
    return "complete"
