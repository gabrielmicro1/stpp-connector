"""MCP server configuration, read from the environment (invariant 7).

Loaded in the app lifespan, never at import time, so tests can construct
Settings directly and the healthz-only import path needs no env.
"""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    rfff_seed_database_url: str
    jwt_secret: str
    wdp_base_url: str
    wdp_auth_token: str
    contracts_dir: Path
    mcp_max_rows: int = 200
    mcp_max_text_chars: int = 2000
    wdp_timeout_seconds: float = 10.0
    enum_ttl_seconds: float = 60.0


def find_contracts_dir() -> Path:
    """CONTRACTS_DIR env (compose mounts /srv/contracts), else walk up from
    this file looking for contracts/mcp-tools (host checkouts)."""
    env = os.getenv("CONTRACTS_DIR")
    if env:
        return Path(env)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "contracts"
        if (candidate / "mcp-tools").is_dir():
            return candidate
    raise FileNotFoundError(
        "contracts/ not found: set CONTRACTS_DIR or run from a checkout"
    )


def load_settings() -> Settings:
    return Settings(
        rfff_seed_database_url=os.environ["RFFF_SEED_DATABASE_URL"],
        jwt_secret=os.environ["JWT_SECRET"],
        wdp_base_url=os.environ["WDP_BASE_URL"],
        wdp_auth_token=os.environ["WDP_AUTH_TOKEN"],
        contracts_dir=find_contracts_dir(),
        mcp_max_rows=int(os.getenv("MCP_MAX_ROWS", "200")),
        mcp_max_text_chars=int(os.getenv("MCP_MAX_TEXT_CHARS", "2000")),
        wdp_timeout_seconds=float(os.getenv("WDP_TIMEOUT_SECONDS", "10")),
        enum_ttl_seconds=float(os.getenv("MCP_ENUM_TTL_SECONDS", "60")),
    )
