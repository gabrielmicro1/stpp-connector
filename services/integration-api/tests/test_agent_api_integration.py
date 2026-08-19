"""End-to-end through the API layer with the REAL agent (scripted LLM +
mock MCP): the SSE stream carries a validated plan, per-step progress, and
a synthesized answer, and the job store matches (write-then-emit)."""
import json

import httpx
import pytest

from agent.mcp_mock import MockMCPFactory
from agent.runner import Agent
from app.main import create_app
from tests.conftest import FakeContextProvider, ScriptedLLM, parse_sse


PLAN = {
    "intent": "research background on the personnel of proposal P-2025-0042",
    "steps": [
        {"id": 1, "tool": "get_proposal",
         "args": {"proposal_number": "P-2025-0042"},
         "reason": "personnel", "depends_on": []},
        {"id": 2, "tool": "search_wdp_person",
         "args": {"orcid": "$steps[1].data.personnel[*].person_orcid"},
         "reason": "wdp summaries", "depends_on": [1]},
    ],
}


@pytest.mark.anyio
async def test_real_agent_streams_validated_plan(
    store, settings, auth_headers, agent_config, planner_inputs, contracts_dir
):
    agent = Agent(
        llm=ScriptedLLM([json.dumps(PLAN), "synthesized answer"]),
        mcp=MockMCPFactory(contracts_dir),
        context=FakeContextProvider(planner_inputs),
        config=agent_config,
    )
    app = create_app(store=store, settings=settings, run_query=agent.run_query)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/query", json={"query": "background on P-2025-0042"},
                headers=auth_headers,
            )
            events = parse_sse(resp.text)
            kinds = [k for k, _ in events]
            assert kinds[0] == "job"
            assert kinds[-1] == "done" and events[-1][1]["status"] == "complete"
            plan_event = next(p for k, p in events if k == "plan")
            assert plan_event["plan"] == PLAN
            step_statuses = [(p["step_id"], p["status"]) for k, p in events if k == "step"]
            assert step_statuses == [
                (1, "running"), (1, "complete"), (2, "running"), (2, "complete")
            ]
            answer = next(p for k, p in events if k == "answer")
            assert answer["text"] == "synthesized answer"
            # wire hygiene: step events never carry tool/args/result
            for k, p in events:
                if k == "step":
                    assert not {"tool", "args", "result"} & p.keys()

            job_id = events[0][1]["job_id"]
            job = (await client.get(f"/v1/jobs/{job_id}", headers=auth_headers)).json()
            assert job["status"] == "complete"
            assert job["plan"] == PLAN
            assert job["answer"] == "synthesized answer"
            # the fanned step persisted the ordered per-call result list
            step2 = next(s for s in job["steps"] if s["step_id"] == 2)
            assert isinstance(step2["result"], list) and len(step2["result"]) == 2
