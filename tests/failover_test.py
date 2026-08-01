"""Unit tests for per-account media failover (quota cooldown + account walk)."""
import asyncio

import pytest

from gemini_openai import chrome_backend as cb


def fake_hub(authusers):
    hub = cb.Hub()
    for i, a in enumerate(authusers, start=1):
        hub.conns[i] = cb.TabConn(ws=object(), key=i, tab_id=f"t{i}", authuser=a)
    return hub


def with_hub(hub):
    saved = cb.hub
    cb.hub = hub
    return saved


def stub_locked(mgr, behavior):
    """behavior: authuser -> exception to raise, or result to return."""
    calls = []

    async def _locked(prompt, kind, timeout=None, aspect=None, authuser=None, _exclude=None):
        calls.append((authuser, timeout))
        b = behavior[authuser]
        if isinstance(b, Exception):
            raise b
        return b

    mgr._generate_media_locked = _locked
    return calls


def test_failover_walks_to_next_account_on_quota_stall():
    saved = with_hub(fake_hub(["2", "3"]))
    try:
        mgr = cb.ChromeManager()
        calls = stub_locked(mgr, {
            "2": RuntimeError("image did not render in time (quota or slow generation)"),
            "3": ([{"kind": "image", "b64": "x"}], ""),
        })

        async def go():
            media, _text, served = await mgr.generate_media("p", "image", timeout=430.0)
            assert served == "3" and media[0]["b64"] == "x"
            assert "2" in mgr.quota_bad and "3" not in mgr.quota_bad
            # the follow-up attempt used the short per-attempt cap, not 430s
            assert calls[1][1] == cb.MEDIA_ATTEMPT_TIMEOUT

        asyncio.run(go())
    finally:
        cb.hub = saved


def test_requested_account_tried_first_and_cooldown_reorders():
    saved = with_hub(fake_hub(["1", "2", "3"]))
    try:
        mgr = cb.ChromeManager()
        assert mgr._media_accounts("2")[0] == "2"
        mgr.quota_bad["1"] = __import__("time").monotonic()
        order = mgr._media_accounts(None)
        assert order.index("1") == len(order) - 1  # in-cooldown account goes last
        assert mgr._media_accounts("1")[0] == "1"  # explicit pin still wins
    finally:
        cb.hub = saved


def test_excluded_account_is_never_a_candidate():
    saved = with_hub(fake_hub(["4"]))
    saved_excl = cb.MEDIA_EXCLUDE_AUTHUSERS
    cb.MEDIA_EXCLUDE_AUTHUSERS = {"4"}
    try:
        mgr = cb.ChromeManager()

        async def go():
            with pytest.raises(RuntimeError, match="no eligible gemini tab"):
                await mgr.generate_media("p", "image")

        asyncio.run(go())
    finally:
        cb.hub = saved
        cb.MEDIA_EXCLUDE_AUTHUSERS = saved_excl


def test_all_accounts_failing_raises_combined_error():
    saved = with_hub(fake_hub(["2", "3"]))
    try:
        mgr = cb.ChromeManager()
        stub_locked(mgr, {
            "2": RuntimeError("media quota exhausted on this account: limit resets"),
            "3": RuntimeError("image did not render in time (quota or slow generation)"),
        })

        async def go():
            with pytest.raises(RuntimeError, match="u/2.*u/3"):
                await mgr.generate_media("p", "image", timeout=430.0)
            assert set(mgr.quota_bad) == {"2", "3"}

        asyncio.run(go())
    finally:
        cb.hub = saved
