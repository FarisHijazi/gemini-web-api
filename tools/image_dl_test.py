"""Are generated IMAGE urls downloadable server-side?

gg-dl links look single-use, so this makes the AUTHENTICATED fetch the FIRST and
ONLY request against the URL (an earlier test fetched anonymously first, which
may have burned the token and caused a misleading 403).
"""

import asyncio
import os

os.environ.setdefault("GEMINI_AUTHUSER", "1")

from gemini_openai.config import resolve_model  # noqa: E402
from gemini_openai.gemini_pool import manager  # noqa: E402


async def main():
    client = await manager.get()
    out = await client.generate_content(
        "Generate an image: a single green apple on a plain white background",
        model=resolve_model("gemini-3-pro"),
    )
    imgs = getattr(out, "images", []) or []
    print(f"images returned: {len(imgs)}")
    if not imgs:
        print("no image (quota?) text:", (out.text or "")[:150])
        await manager.reset()
        return
    url = imgs[0].url
    print("url host:", url.split("/")[2])

    # FIRST and ONLY request: authenticated session, gemini referer.
    r = await client.client.get(
        url, headers={"Referer": "https://gemini.google.com/"}, timeout=60
    )
    print(f"  authenticated (first hit) -> HTTP {r.status_code} bytes={len(r.content)} "
          f"magic={r.content[:8]!r}")
    if r.status_code == 200 and len(r.content) > 1000:
        os.makedirs("media", exist_ok=True)
        with open("media/image_dl_test.png", "wb") as f:
            f.write(r.content)
        print("  ✅ SAVED media/image_dl_test.png — images ARE server-side downloadable")
    else:
        print("  ✗ still blocked")

    # Now probe reuse: a second identical request (is the link single-use?)
    r2 = await client.client.get(
        url, headers={"Referer": "https://gemini.google.com/"}, timeout=60
    )
    print(f"  second hit                -> HTTP {r2.status_code} bytes={len(r2.content)} "
          f"(single-use? {r.status_code == 200 and r2.status_code != 200})")

    await manager.reset()


asyncio.run(main())
