"""Query agent module.

Lives in the integration-api process but MUST NOT import from the API layer
(app/) except shared types — it must stay extractable to its own service
(CLAUDE.md invariant 3).
"""
