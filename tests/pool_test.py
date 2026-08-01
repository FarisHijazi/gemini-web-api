"""Unit tests for the chrome_backend tab POOL (acquire/release semantics)."""
import asyncio

import pytest

from gemini_openai import chrome_backend as cb


def make_hub_with(tabs):
    hub = cb.Hub()
    for i, authuser in enumerate(tabs, start=1):
        hub.conns[i] = cb.TabConn(ws=object(), key=i, tab_id=f"t{i}", authuser=authuser)
    return hub


def test_acquire_prefers_free_tab():
    hub = make_hub_with(["0", "1"])

    async def go():
        c1 = await hub.acquire()
        c2 = await hub.acquire()
        assert {c1.key, c2.key} == {1, 2}
        assert c1.busy and c2.busy

    asyncio.run(go())


def test_acquire_no_tabs_raises_immediately():
    hub = cb.Hub()

    async def go():
        with pytest.raises(RuntimeError, match="no eligible gemini tab"):
            await hub.acquire()

    asyncio.run(go())


def test_acquire_strict_authuser():
    hub = make_hub_with(["0", "1"])

    async def go():
        c = await hub.acquire(authuser="1")
        assert c.authuser == "1"
        # no tab on u/7 -> immediate, named error (never falls back silently)
        with pytest.raises(RuntimeError, match="u/7"):
            await hub.acquire(authuser="7")

    asyncio.run(go())


def test_acquire_waits_for_release():
    hub = make_hub_with(["0"])

    async def go():
        c = await hub.acquire()
        got = asyncio.create_task(hub.acquire())
        await asyncio.sleep(0.05)
        assert not got.done()  # queued: the only tab is busy
        hub.release(c)
        c2 = await asyncio.wait_for(got, timeout=2)
        assert c2.key == c.key and c2.busy

    asyncio.run(go())


def test_parallel_acquire_never_double_books():
    hub = make_hub_with(["0", "0", "0"])

    async def go():
        conns = await asyncio.gather(*[hub.acquire() for _ in range(3)])
        assert len({c.key for c in conns}) == 3

    asyncio.run(go())
