"""Agent-side configuration, read from the environment (invariant 7).

Mirrors app/config.py but lives in the agent module: the agent must not
import anything under app/ (invariant 3). Loaded per run, never at import
time, so tests can construct AgentConfig directly.
"""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentConfig:
    llm_provider: str
    llm_model: str
    llm_api_key: str
    llm_base_url: str | None
    llm_max_tokens: int
    aws_region: str | None
    plan_max_steps: int
    plan_max_fanout: int
    planner_max_matches: int
    chat_replay_chars: int
    rfff_seed_database_url: str
    contracts_dir: Path
    # Defaulted so existing direct constructions keep working; the loader
    # below still hard-requires MCP_SERVER_URL from the environment.
    mcp_server_url: str = ""
    mcp_timeout_seconds: float = 30.0


def find_contracts_dir() -> Path:
    """CONTRACTS_DIR env (compose mounts /srv/contracts), else walk up from
    this file looking for contracts/plan-format.json (host checkouts)."""
    env = os.getenv("CONTRACTS_DIR")
    if env:
        return Path(env)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "contracts"
        if (candidate / "plan-format.json").is_file():
            return candidate
    raise FileNotFoundError(
        "contracts/ not found: set CONTRACTS_DIR or run from a checkout"
    )


def load_agent_config() -> AgentConfig:
    return AgentConfig(
        llm_provider=os.environ["LLM_PROVIDER"],
        llm_model=os.environ["LLM_MODEL"],
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_base_url=os.getenv("LLM_BASE_URL") or None,
        llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
        aws_region=os.getenv("AWS_REGION") or None,
        plan_max_steps=int(os.getenv("PLAN_MAX_STEPS", "8")),
        plan_max_fanout=int(os.getenv("PLAN_MAX_FANOUT", "10")),
        planner_max_matches=int(os.getenv("PLANNER_MAX_MATCHES", "20")),
        chat_replay_chars=int(os.getenv("CHAT_REPLAY_CHARS", "2000")),
        rfff_seed_database_url=os.environ["RFFF_SEED_DATABASE_URL"],
        contracts_dir=find_contracts_dir(),
        mcp_server_url=os.environ["MCP_SERVER_URL"],
        mcp_timeout_seconds=float(os.getenv("MCP_TIMEOUT_SECONDS", "30")),
    )
