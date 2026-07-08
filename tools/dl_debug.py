"""Debug the video download: the URL is valid (plays in browser). Find the
header/cookie combo that makes curl_cffi fetch it."""

import asyncio
import os

os.environ["GEMINI_AUTHUSER"] = "6"
from gemini_openai.gemini_pool import manager  # noqa: E402

URL = open("/tmp/claude-1000/-mnt-d-home-Projects-gemini-scraper/a5dc857e-7431-4552-865a-c41744e927e7/scratchpad/vidurl.txt").read().strip()


async def attempt(sess, name, headers):
    try:
        r = await sess.get(URL, headers=headers, timeout=60)
        body = r.content[:200]
        ismp4 = b"ftyp" in r.content[:64]
        print(f"  {name:26} -> {r.status_code} ct={r.headers.get('content-type','')[:24]} bytes={len(r.content)} mp4={ismp4}", flush=True)
        if r.status_code == 403:
            # extract a hint from the html
            txt = r.content.decode("utf-8", "replace")
            import re
            m = re.search(r"<title>(.*?)</title>|<p[^>]*>(.*?)</p>", txt)
            print(f"       403 hint: {(m.group(0)[:100] if m else txt[:100])!r}", flush=True)
        return ismp4, r.status_code
    except Exception as e:  # noqa: BLE001
        print(f"  {name:26} -> EXC {str(e)[:50]}", flush=True)
        return False, None


async def main():
    client = await manager.get()
    sess = client.client
    base_ref = {"Referer": "https://gemini.google.com/"}
    tests = {
        "plain+referer": base_ref,
        "range": {**base_ref, "Range": "bytes=0-"},
        "sec-fetch-video": {**base_ref, "Range": "bytes=0-",
                            "Sec-Fetch-Dest": "video", "Sec-Fetch-Mode": "no-cors",
                            "Sec-Fetch-Site": "same-site",
                            "Accept": "*/*"},
        "no-referer": {},
        "origin": {**base_ref, "Origin": "https://gemini.google.com"},
    }
    print("cookies on session:", len(list(sess.cookies.jar)) if hasattr(sess.cookies, "jar") else "?", flush=True)
    for name, h in tests.items():
        ok, st = await attempt(sess, name, h)
        if ok:
            open("media/dl_debug.mp4", "wb")
            print("   SUCCESS with", name, flush=True)
            break
    await manager.reset()


asyncio.run(main())
