import jwt as pyjwt
import pytest

from mint_jwt import TEST_USERS, mint


def test_two_standing_users_and_claims(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("JWT_TTL_HOURS", "1")
    assert set(TEST_USERS) == {"analyst-full", "analyst-local"}
    token = mint("analyst-full")
    claims = pyjwt.decode(token, "test-secret", algorithms=["HS256"])
    assert claims["sub"] == "analyst-full"
    assert claims["roles"] == ["rfff_reader", "wdp_reader"]
    assert claims["component"]
    assert claims["name"]
    assert claims["exp"] - claims["iat"] == 3600
    local = pyjwt.decode(mint("analyst-local"), "test-secret", algorithms=["HS256"])
    assert local["roles"] == ["rfff_reader"]


def test_default_ttl_is_720_hours(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.delenv("JWT_TTL_HOURS", raising=False)
    claims = pyjwt.decode(mint("analyst-full"), "test-secret", algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 720 * 3600


def test_unknown_user_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    with pytest.raises(KeyError):
        mint("nobody")
