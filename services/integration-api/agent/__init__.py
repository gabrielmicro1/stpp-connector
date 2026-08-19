"""Query agent module.

Lives in the integration-api process but MUST NOT import from the API layer
(app/) except shared types — it must stay extractable to its own service
(CLAUDE.md invariant 3).
"""
from .interface import EventSink, Outcome
from .stub import run_query

__all__ = ["EventSink", "Outcome", "run_query"]
