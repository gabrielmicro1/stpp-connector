"""Types shared between the API layer and the agent module.

The agent may import from here and NOTHING else under app/ (CLAUDE.md
invariant 3): this module is the entire shared surface.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class UserContext:
    """Delegated user context from the validated JWT, threaded through
    every layer (invariant 5)."""

    sub: str
    name: str
    component: str
    roles: tuple[str, ...]
