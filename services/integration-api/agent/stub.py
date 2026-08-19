"""Phase-4 stub agent: canned plan + fake progress with delays.

Phase 6 replaces this module's internals with the real planner/executor;
the run_query signature and EventSink protocol in interface.py are the
permanent contract and MUST NOT change shape.
"""
import asyncio
import os

from shared.types import UserContext

from .interface import EventSink, Outcome


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


async def run_query(query: str, user: UserContext, sink: EventSink) -> Outcome:
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
        "[stub] Canned answer for demo purposes — the real agent lands in "
        f"phase 6. Your query was: {query!r} (run as {user.sub}, "
        f"roles={list(user.roles)})."
    )
    return "complete"
