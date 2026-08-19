import json
import time

import httpx
import jwt
import pytest

from agent.config import find_contracts_dir
from app.config import Settings
from app.main import create_app
from tests.memory_store import MemoryJobStore

TEST_SECRET = "test-secret"


def make_token(secret=TEST_SECRET, exp_delta=3600, **overrides):
    claims = {
        "sub": "analyst-full",
        "name": "Avery Fullaccess",
        "component": "DARPA",
        "roles": ["rfff_reader", "wdp_reader"],
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_delta,
    }
    claims.update(overrides)
    return jwt.encode(claims, secret, algorithm="HS256")


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def settings():
    return Settings(
        jobs_database_url="unused-in-tests",
        jwt_secret=TEST_SECRET,
        sse_ping_seconds=15.0,
        jobs_retention_hours=24,
        job_max_seconds=120.0,
    )


@pytest.fixture
def store():
    return MemoryJobStore()


@pytest.fixture
def app(store, settings):
    from tests import fake_agent

    return create_app(store=store, settings=settings, run_query=fake_agent.run_query)


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {make_token()}"}


@pytest.fixture
async def lifespan_client(app):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture(scope="session")
def contracts_dir():
    return find_contracts_dir()


@pytest.fixture(scope="session")
def plan_schema(contracts_dir):
    return json.loads((contracts_dir / "plan-format.json").read_text())


@pytest.fixture(scope="session")
def tool_defs(contracts_dir):
    """All seven MCP tool definitions from the frozen contracts, sorted by
    name — the shape a session's tools_list() returns."""
    return [
        json.loads(path.read_text())
        for path in sorted((contracts_dir / "mcp-tools").glob("*.json"))
    ]


@pytest.fixture
def observed_enums():
    """Fixture observed-enum sets (seed-shaped, not seed-derived)."""
    return {
        "fiscal_year": {"2024", "2025"},
        "reviewing_component": {"DARPA", "DTRA", "ONR"},
        "reviewing_subcomponent": {"DARPA-BTO"},
        "assessment_state": {"Complete", "In Review"},
        "mitigation_status": {"Complete", "Implemented"},
        "award_state": {"Awarded", "Declined"},
        "award_type": {"Grant"},
        "review_type": {"Standard"},
        "factor1_assessment": {"No Concerns"},
        "factor2_assessment": {"No Concerns"},
        "factor3_assessment": {"No Concerns"},
        "factor4_assessment": {"No Concerns", "Prohibited Factors"},
        "person_overall_assessment": {"High", "Low"},
    }


class RecordingSink:
    """Implements agent.EventSink; records every call for assertions."""

    def __init__(self):
        self.events = []

    async def status(self, status):
        self.events.append(("status", status))

    async def plan(self, plan):
        self.events.append(("plan", plan))

    async def step(self, step_id, status, **kwargs):
        self.events.append(("step", step_id, status, kwargs))

    async def answer(self, text):
        self.events.append(("answer", text))

    async def error(self, code, message):
        self.events.append(("error", code, message))

    def kinds(self):
        return [e[0] for e in self.events]

    def steps(self):
        return [(e[1], e[2], e[3]) for e in self.events if e[0] == "step"]


class ScriptedLLM:
    """Implements agent.llm.LLMClient; returns queued responses (or raises
    queued exceptions) and records every prompt."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []
        self.json_modes = []

    async def complete(self, prompt, *, json_mode=False):
        self.prompts.append(prompt)
        self.json_modes.append(json_mode)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeContextProvider:
    def __init__(self, inputs):
        self._inputs = inputs

    async def load(self, query):
        return self._inputs


@pytest.fixture
def recording_sink():
    return RecordingSink()


@pytest.fixture
def planner_inputs(observed_enums):
    from agent.context import PlannerInputs

    return PlannerInputs(
        planner_context={
            "fields": {},
            "caveats": ["person_overall_assessment is opaque; report verbatim."],
            "profile": {"row_count": 340},
        },
        catalog_matches=[],
        observed_enums=observed_enums,
    )


@pytest.fixture
def agent_config(contracts_dir):
    from agent.config import AgentConfig

    return AgentConfig(
        llm_provider="gemini",
        llm_model="scripted",
        llm_api_key="unused",
        llm_base_url=None,
        llm_max_tokens=4096,
        aws_region=None,
        plan_max_steps=8,
        plan_max_fanout=10,
        planner_max_matches=20,
        rfff_seed_database_url="unused-in-tests",
        contracts_dir=contracts_dir,
    )
