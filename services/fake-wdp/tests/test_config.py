from app.config import load_settings


def test_load_settings_parses_env(monkeypatch):
    monkeypatch.setenv("WDP_DATABASE_URL", "postgresql://x/wdp")
    monkeypatch.setenv("WDP_FAKE_TOKEN", "tok")
    monkeypatch.setenv("FAKE_WDP_DENY_ORCIDS", " 0000-1, ,0000-2,")
    settings = load_settings()
    assert settings.wdp_database_url == "postgresql://x/wdp"
    assert settings.wdp_fake_token == "tok"
    assert settings.deny_orcids == frozenset({"0000-1", "0000-2"})


def test_load_settings_empty_deny_list(monkeypatch):
    monkeypatch.setenv("WDP_DATABASE_URL", "postgresql://x/wdp")
    monkeypatch.setenv("WDP_FAKE_TOKEN", "tok")
    monkeypatch.delenv("FAKE_WDP_DENY_ORCIDS", raising=False)
    assert load_settings().deny_orcids == frozenset()
