import pytest

from agent.config import find_contracts_dir, load_agent_config


BASE_ENV = {
    "LLM_PROVIDER": "gemini",
    "LLM_MODEL": "gemini-test",
    "LLM_API_KEY": "k",
    "RFFF_SEED_DATABASE_URL": "postgresql://x/rfff_seed",
    "MCP_SERVER_URL": "http://mcp-server:8001",
}


# Optional vars with defaults under test: clear them so values inherited from
# the container env (compose passes .env through) can't leak into assertions.
OPTIONAL_VARS = (
    "LLM_API_KEY", "LLM_BASE_URL", "LLM_MAX_TOKENS", "AWS_REGION",
    "PLAN_MAX_STEPS", "PLAN_MAX_FANOUT", "PLANNER_MAX_MATCHES",
    "MCP_TIMEOUT_SECONDS",
)


def _set_env(monkeypatch, extra=None):
    for key in OPTIONAL_VARS:
        monkeypatch.delenv(key, raising=False)
    for key, value in {**BASE_ENV, **(extra or {})}.items():
        monkeypatch.setenv(key, value)


def test_defaults(monkeypatch):
    _set_env(monkeypatch)
    cfg = load_agent_config()
    assert cfg.llm_provider == "gemini"
    assert cfg.llm_model == "gemini-test"
    assert cfg.llm_base_url is None
    assert cfg.llm_max_tokens == 4096
    assert cfg.plan_max_steps == 8
    assert cfg.plan_max_fanout == 10
    assert cfg.planner_max_matches == 20
    assert cfg.rfff_seed_database_url == "postgresql://x/rfff_seed"
    assert cfg.mcp_server_url == "http://mcp-server:8001"
    assert cfg.mcp_timeout_seconds == 30.0


def test_overrides(monkeypatch):
    _set_env(
        monkeypatch,
        {
            "LLM_BASE_URL": "http://llm.local",
            "LLM_MAX_TOKENS": "512",
            "AWS_REGION": "us-gov-west-1",
            "PLAN_MAX_STEPS": "3",
            "PLAN_MAX_FANOUT": "2",
            "PLANNER_MAX_MATCHES": "5",
            "MCP_TIMEOUT_SECONDS": "7.5",
        },
    )
    cfg = load_agent_config()
    assert cfg.llm_base_url == "http://llm.local"
    assert cfg.llm_max_tokens == 512
    assert cfg.aws_region == "us-gov-west-1"
    assert cfg.plan_max_steps == 3
    assert cfg.plan_max_fanout == 2
    assert cfg.planner_max_matches == 5
    assert cfg.mcp_timeout_seconds == 7.5


def test_contracts_dir_from_env(monkeypatch, tmp_path):
    (tmp_path / "plan-format.json").write_text("{}")
    monkeypatch.setenv("CONTRACTS_DIR", str(tmp_path))
    assert find_contracts_dir() == tmp_path


def test_contracts_dir_walk_up(monkeypatch):
    monkeypatch.delenv("CONTRACTS_DIR", raising=False)
    found = find_contracts_dir()
    assert (found / "plan-format.json").is_file()


def test_error_codes():
    from agent.errors import (
        BudgetExceededError,
        LLMUnavailableError,
        MCPToolError,
        PlanInvalidError,
    )

    e = PlanInvalidError(["dup id", "bad tool"])
    assert e.code == "plan_invalid"
    assert e.violations == ["dup id", "bad tool"]
    assert "dup id" in str(e)
    assert LLMUnavailableError("x").code == "llm_unavailable"
    assert BudgetExceededError("x").code == "budget_exceeded"
    t = MCPToolError("not_found", "no such proposal")
    assert t.code == "not_found"
    assert "no such proposal" in str(t)
