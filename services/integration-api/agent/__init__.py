"""Query agent module.

Lives in the integration-api process but MUST NOT import from the API layer
(app/) except shared types — it must stay extractable to its own service
(CLAUDE.md invariant 3).

run_query keeps the frozen (query, user, sink) -> Outcome contract, extended
in phase 9 with an optional keyword-only `history` (prior conversation turns,
oldest first, as {role, content} dicts — untrusted context for prompt
assembly only); the default Agent behind it is built lazily from the
environment. Phase 7 wires
the HTTP MCP client (MCP_SERVER_URL); tests keep injecting MockMCPFactory
directly.
"""
from .interface import EventSink, Outcome
from shared.types import UserContext


_default_agent = None


def _build_default():
    from .config import load_agent_config
    from .context import PostgresContextProvider
    from .llm import make_llm_client
    from .mcp_http import HTTPMCPFactory
    from .runner import Agent

    config = load_agent_config()
    return Agent(
        llm=make_llm_client(config),
        mcp=HTTPMCPFactory(
            config.mcp_server_url, timeout=config.mcp_timeout_seconds
        ),
        context=PostgresContextProvider(
            config.rfff_seed_database_url, config.planner_max_matches
        ),
        config=config,
    )


async def run_query(
    query: str, user: UserContext, sink: EventSink, *, history: list[dict] = ()
) -> Outcome:
    global _default_agent
    if _default_agent is None:
        _default_agent = _build_default()
    return await _default_agent.run_query(query, user, sink, history=history)


def reset_default_agent() -> None:
    """Test hook: drop the lazily-built default wiring."""
    global _default_agent
    _default_agent = None


__all__ = ["EventSink", "Outcome", "run_query", "reset_default_agent"]
