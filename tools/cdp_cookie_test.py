"""Verify CDP cookie harvesting (incl. httpOnly) against a throwaway Chrome.

Plants a fake __Secure-1PSID as an httpOnly cookie via CDP, then reads it back
with video_bridge.fetch_cookies + config.get_cookies to prove the fallback path
works without any manual DevTools copy-paste.
"""

import asyncio
import os

import websockets

from gemini_openai import video_bridge as vb

CDP = os.getenv("CDP_TEST_URL", "http://localhost:9334")
FAKE_PSID = "g.a000_FAKE_TEST_PSID_VALUE_12345"
FAKE_PSIDTS = "sidts-FAKE_TEST_TS_67890"


async def plant_cookies():
    ws_url = vb._browser_ws_url(CDP)
    async with websockets.connect(ws_url, max_size=None, open_timeout=10) as ws:
        cdp = vb._CDP(ws)
        try:
            for name, value in (("__Secure-1PSID", FAKE_PSID),
                                ("__Secure-1PSIDTS", FAKE_PSIDTS),
                                ("SAPISID", "fake-sapisid")):
                await cdp.send("Storage.setCookies", {"cookies": [{
                    "name": name, "value": value, "domain": ".google.com",
                    "path": "/", "secure": True, "httpOnly": True,
                }]})
            print("planted 3 httpOnly cookies on .google.com")
        finally:
            cdp.close()


async def main():
    await plant_cookies()

    jar = await vb.fetch_cookies(CDP)
    print(f"fetch_cookies -> {len(jar)} google cookies")
    print("  __Secure-1PSID  :", "FOUND" if jar.get("__Secure-1PSID") == FAKE_PSID else "MISSING")
    print("  __Secure-1PSIDTS:", "FOUND" if jar.get("__Secure-1PSIDTS") == FAKE_PSIDTS else "MISSING")
    assert jar.get("__Secure-1PSID") == FAKE_PSID, "httpOnly cookie not retrieved!"

    # Now prove the config fallback uses it when the local store is unreadable.
    import gemini_openai.config as config
    config.CDP_URL = CDP
    config.CHROME_DIR = "/tmp/definitely-no-chrome-here"
    psid, psidts = config.get_cookies()
    print(f"config.get_cookies (no local store) -> psid={'OK' if psid == FAKE_PSID else 'FAIL'} "
          f"psidts={'OK' if psidts == FAKE_PSIDTS else 'FAIL'}")
    full = config.get_full_jar()
    print(f"config.get_full_jar -> {len(full)} cookies")
    assert psid == FAKE_PSID and len(full) >= 3
    print("\n✅ CDP cookie fallback works — no manual cookie copying needed")


asyncio.run(main())
