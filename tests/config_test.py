"""Unit tests for model resolution and Google-account selection.

The account tests exist because the server used to pick whichever Chrome profile
had been touched most recently, so with several accounts signed in it silently
spoke as a work account -- and which one changed between restarts.
"""
import json
import os

import pytest

from gemini_openai import config


# --------------------------------------------------------------------------- #
# Model resolution
# --------------------------------------------------------------------------- #
def test_resolve_model_returns_names_not_enum_members():
    # Enum members are deprecated upstream and pending removal; a name string
    # resolves on every version we support.
    for name in config.list_public_models():
        assert isinstance(config.resolve_model(name), str)


@pytest.mark.parametrize(
    "asked,expected",
    [
        ("gemini-3-pro", "gemini-3-pro"),
        ("GEMINI-3-PRO", "gemini-3-pro"),
        ("models/gemini-3-pro", "gemini-3-pro"),
        ("  gemini-3-flash  ", "gemini-3-flash"),
        ("gpt-4o", "gemini-3-pro"),
        ("gpt-3.5-turbo", "gemini-3-flash"),
        ("no-such-model", "gemini-3-flash"),
        (None, "gemini-3-flash"),
    ],
)
def test_resolve_model_aliases_and_fallback(asked, expected):
    assert config.resolve_model(asked) == expected


def test_thinking_tier_is_never_advertised_when_absent():
    # 2.1 removed the thinking tier (*_LITE is a new cheap tier, not a rename).
    # Where it is gone we must serve flash but must NOT list it as available.
    advertised = config.list_public_models()
    if config._THINKING is None:
        assert not any("thinking" in m for m in advertised)
        assert config.resolve_model("gemini-3-flash-thinking") == "gemini-3-flash"
    else:
        assert "gemini-3-flash-thinking" in advertised


# --------------------------------------------------------------------------- #
# Google account selection
# --------------------------------------------------------------------------- #
@pytest.fixture
def chrome(tmp_path, monkeypatch):
    """A fake Chrome user-data dir with three signed-in profiles."""
    accounts = {
        "Default": "owner@example.com",
        "Profile 3": "work@acme.example",
        "Profile 7": "other@acme.example",
    }
    for prof in accounts:
        (tmp_path / prof).mkdir()
        (tmp_path / prof / "Cookies").write_text("")
    (tmp_path / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {p: {"user_name": e} for p, e in accounts.items()}}})
    )
    monkeypatch.setattr(config, "CHROME_DIR", str(tmp_path))
    for var in ("GEMINI_CHROME_ACCOUNT", "GEMINI_CHROME_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_pinned_account_selects_exactly_one_profile(chrome, monkeypatch):
    monkeypatch.setenv("GEMINI_CHROME_ACCOUNT", "owner@example.com")
    stores = config._cookie_stores()
    assert len(stores) == 1
    assert config.account_of(stores[0]) == "owner@example.com"


def test_account_pin_is_case_and_space_insensitive(chrome, monkeypatch):
    monkeypatch.setenv("GEMINI_CHROME_ACCOUNT", "  Owner@Example.COM ")
    assert config.account_of(config._cookie_stores()[0]) == "owner@example.com"


def test_unknown_pinned_account_raises_rather_than_falling_back(chrome, monkeypatch):
    # The dangerous behaviour would be quietly using some other account.
    monkeypatch.setenv("GEMINI_CHROME_ACCOUNT", "typo@example.com")
    with pytest.raises(RuntimeError, match="not signed in to Chrome"):
        config._cookie_stores()


def test_profile_pin_takes_precedence_over_account(chrome, monkeypatch):
    monkeypatch.setenv("GEMINI_CHROME_PROFILE", "Profile 3")
    monkeypatch.setenv("GEMINI_CHROME_ACCOUNT", "owner@example.com")
    assert config.account_of(config._cookie_stores()[0]) == "work@acme.example"


def test_unpinned_order_is_deterministic(chrome):
    # Same mtime on every store: without a tiebreak the order is arbitrary, and
    # the account would change between restarts.
    for prof in ("Default", "Profile 3", "Profile 7"):
        os.utime(chrome / prof / "Cookies", (1_700_000_000, 1_700_000_000))
    assert config._cookie_stores() == config._cookie_stores()
    assert len(set(config._cookie_stores())) == 3


# --------------------------------------------------------------------------- #
# Who owns refreshing the session cookie
# --------------------------------------------------------------------------- #
def test_chrome_cookies_mean_chrome_owns_the_refresh(monkeypatch):
    # Rotating a session Chrome also holds logs the browser out of that account,
    # so reading from Chrome must never rotate.
    monkeypatch.delenv("GEMINI_1PSID", raising=False)
    monkeypatch.delenv("GEMINI_AUTO_REFRESH", raising=False)
    assert config.rotate_cookies_ourselves() is False


def test_explicit_cookies_mean_we_must_refresh(monkeypatch):
    # No browser owns these, so nobody else will keep them alive.
    monkeypatch.setenv("GEMINI_1PSID", "dummy")
    monkeypatch.delenv("GEMINI_AUTO_REFRESH", raising=False)
    assert config.rotate_cookies_ourselves() is True


@pytest.mark.parametrize(
    "value,expected",
    [("1", True), ("true", True), ("yes", True), ("0", False), ("false", False),
     ("off", False), ("", False)],
)
def test_auto_refresh_override_wins(monkeypatch, value, expected):
    monkeypatch.delenv("GEMINI_1PSID", raising=False)
    monkeypatch.setenv("GEMINI_AUTO_REFRESH", value)
    assert config.rotate_cookies_ourselves() is expected
