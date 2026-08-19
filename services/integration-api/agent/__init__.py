"""Query agent module.

Lives in the integration-api process but MUST NOT import from the API layer
(app/) except shared types — it must stay extractable to its own service
(CLAUDE.md invariant 3).

run_query keeps the frozen (query, user, sink) -> Outcome contract; the
default Agent behind it is built lazily from the environment. Phase 6 wires
the mock MCP factory; phase 7 swaps in the HTTP MCP client here without
touching the interface.
"""
from .interface import EventSink, Outcome
from shared.types import UserContext


_default_agent = None


def _build_default():
    from .config import load_agent_config
    from .context import PostgresContextProvider
    from .llm import make_llm_client
    from .mcp_mock import MockMCPFactory
    from .runner import Agent

    config = load_agent_config()
    return Agent(
        llm=make_llm_client(config),
        # phase 7: replace with the HTTP MCP client (MCP_SERVER_URL)
        mcp=MockMCPFactory(config.contracts_dir),
        context=PostgresContextProvider(
            config.rfff_seed_database_url, config.planner_max_matches
        ),
        config=config,
    )


async def run_query(query: str, user: UserContext, sink: EventSink) -> Outcome:
    global _default_agent
    if _default_agent is None:
        _default_agent = _build_default()
    return await _default_agent.run_query(query, user, sink)


def reset_default_agent() -> None:
    """Test hook: drop the lazily-built default wiring."""
    global _default_agent
    _default_agent = None


__all__ = ["EventSink", "Outcome", "run_query", "reset_default_agent"]
