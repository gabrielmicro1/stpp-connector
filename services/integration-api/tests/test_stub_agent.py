import json
import pathlib

import jsonschema
import pytest

from agent import run_query
from shared.types import UserContext

# Repo checkout: tests/ -> integration-api -> services -> repo root. Inside
# the Docker image the service root is /srv, so parents[3] does not exist and
# the conformance test skips.
_parents = pathlib.Path(__file__).resolve().parents
CONTRACTS_DIR = (_parents[3] / "contracts") if len(_parents) > 3 else pathlib.Path("/nonexistent")

USER = UserContext(
    sub="analyst-full", name="Avery", component="DARPA",
    roles=("rfff_reader", "wdp_reader"),
)


class RecordingSink:
    def __init__(self):
        self.calls = []

    async def status(self, status):
        self.calls.append(("status", status))

    async def plan(self, plan):
        self.calls.append(("plan", plan))

    async def step(self, step_id, status, **kw):
        self.calls.append(("step", step_id, status, kw))

    async def answer(self, text):
        self.calls.append(("answer", text))

    async def error(self, code, message):
        self.calls.append(("error", code, message))


@pytest.mark.anyio
async def test_stub_emits_full_lifecycle(monkeypatch):
    monkeypatch.setenv("AGENT_STUB_STEP_SECONDS", "0")
    sink = RecordingSink()
    outcome = await run_query("how many proposals in FY2024?", USER, sink)
    assert outcome == "complete"
    kinds = [c[0] for c in sink.calls]
    # deterministic stub: plan, executing, 2x(running+complete), synthesizing, answer
    assert kinds == ["plan", "status", "step", "step", "step", "step", "status", "answer"]
    assert [c[1] for c in sink.calls if c[0] == "status"] == ["executing", "synthesizing"]

    plan = sink.calls[0][1]
    assert plan["steps"][0]["tool"] == "search_proposals"
    assert plan["steps"][1]["tool"] == "get_proposal"

    step_calls = [c for c in sink.calls if c[0] == "step"]
    assert [(c[1], c[2]) for c in step_calls] == [
        (1, "running"), (1, "complete"), (2, "running"), (2, "complete"),
    ]
    for c in step_calls:
        assert c[3]["tool"] in ("search_proposals", "get_proposal")
    completes = [c for c in step_calls if c[2] == "complete"]
    for c in completes:
        assert c[3]["rows"] is not None
        assert c[3]["truncated"] is False
        assert c[3]["result"] is not None
        assert c[3]["summary"]

    answer = next(c[1] for c in sink.calls if c[0] == "answer")
    assert "stub" in answer.lower()


@pytest.mark.anyio
@pytest.mark.skipif(not CONTRACTS_DIR.exists(), reason="contracts/ not mounted (in-image run)")
async def test_canned_plan_conforms_to_plan_format(monkeypatch):
    monkeypatch.setenv("AGENT_STUB_STEP_SECONDS", "0")
    sink = RecordingSink()
    await run_query("q", USER, sink)
    plan = next(c[1] for c in sink.calls if c[0] == "plan")
    schema = json.loads((CONTRACTS_DIR / "plan-format.json").read_text())
    jsonschema.validate(plan, schema)
