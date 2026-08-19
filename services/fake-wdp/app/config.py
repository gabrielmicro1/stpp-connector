import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    wdp_database_url: str
    wdp_fake_token: str
    deny_orcids: frozenset[str]


def load_settings() -> Settings:
    """Read settings from the environment (invariant 7). Called only from
    the app lifespan, never at import time, so tests can construct Settings
    directly. FAKE_WDP_DENY_ORCIDS is a comma-separated list; empty entries
    are stripped."""
    deny_raw = os.getenv("FAKE_WDP_DENY_ORCIDS", "")
    return Settings(
        wdp_database_url=os.environ["WDP_DATABASE_URL"],
        wdp_fake_token=os.environ["WDP_FAKE_TOKEN"],
        deny_orcids=frozenset(part.strip() for part in deny_raw.split(",") if part.strip()),
    )
