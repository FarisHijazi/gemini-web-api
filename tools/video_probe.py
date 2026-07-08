"""Probe whether this account can generate a Veo video via the web client."""

import asyncio
import sys
import time

from gemini_webapi import GeminiClient
from gemini_webapi.constants import Model

sys.path.insert(0, "tools")
from extract_cookies import extract  # noqa: E402


async def main() -> None:
    creds = extract()
    client = GeminiClient(creds["__Secure-1PSID"], creds["__Secure-1PSIDTS"])
    await client.init(timeout=180, auto_refresh=False)
    print("init OK", flush=True)

    t0 = time.time()
    out = await client.generate_content(
        "Create a short 8-second cinematic video of a red fox running through a snowy forest at sunrise.",
        model=Model.BASIC_PRO,
    )
    dt = time.time() - t0
    print(f"generate returned in {dt:.0f}s", flush=True)
    print("text:", repr((out.text or "")[:300]), flush=True)
    print("num videos:", len(out.videos or []), flush=True)
    print("num images:", len(out.images or []), flush=True)
    for i, v in enumerate(out.videos or []):
        print(f"video[{i}] url={v.url!r} title={v.title!r} thumb={getattr(v,'thumbnail',None)!r}", flush=True)
    # try saving (polls until ready)
    if out.videos:
        try:
            p = await out.videos[0].save(path="tmp_videos", verbose=True)
            print("saved:", p, flush=True)
        except Exception as e:  # noqa: BLE001
            print("save error:", repr(e), flush=True)
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
