"""Probe the usercontent download OSID handshake.

The video host (contribution-*.usercontent.google.com) needs a host-scoped OSID
cookie the browser mints on first access. Find out how: does hitting the URL
without redirects return a 302 to an accounts endpoint that Set-Cookies the OSID?
"""

import asyncio
import os
import sys

os.environ.setdefault("GEMINI_AUTHUSER", "6")
from gemini_openai.gemini_pool import manager  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else open(
    "/tmp/claude-1000/-mnt-d-home-Projects-gemini-scraper/"
    "a5dc857e-7431-4552-865a-c41744e927e7/scratchpad/vidurl.txt"
).read().strip()


async def main():
    client = await manager.get()
    sess = client.client  # curl_cffi AsyncSession w/ full .google.com jar
    print("URL host:", URL.split("/")[2], flush=True)

    # 1. No-redirect probe: what does the server do?
    for allow in (False, True):
        try:
            r = await sess.get(
                URL,
                headers={"Referer": "https://gemini.google.com/",
                         "Sec-Fetch-Dest": "video", "Sec-Fetch-Mode": "no-cors",
                         "Sec-Fetch-Site": "cross-site", "Accept": "*/*"},
                allow_redirects=allow, timeout=60,
            )
            loc = r.headers.get("location", "")
            sc = r.headers.get("set-cookie", "")
            print(f"\nallow_redirects={allow}: {r.status_code} bytes={len(r.content)}", flush=True)
            print(f"  location: {loc[:200]}", flush=True)
            print(f"  set-cookie: {sc[:200]}", flush=True)
            if hasattr(r, "history") and r.history:
                for h in r.history:
                    print(f"  hist: {h.status_code} {h.url[:120]}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"allow_redirects={allow}: EXC {type(e).__name__}: {str(e)[:120]}", flush=True)

    await manager.reset()


asyncio.run(main())
