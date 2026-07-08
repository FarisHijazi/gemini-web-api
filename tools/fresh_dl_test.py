"""Definitive test: generate a video on an account WITH quota, then try the
server-side download IMMEDIATELY (rules out URL-expiry as the 403 cause).

Usage: PYTHONPATH=. GEMINI_AUTHUSER=1 uv run --active python tools/fresh_dl_test.py 1
"""

import asyncio
import os
import sys
import time

N = sys.argv[1] if len(sys.argv) > 1 else "1"
os.environ["GEMINI_AUTHUSER"] = N

import gemini_openai.config as config  # noqa: E402
from gemini_openai import video as vmod  # noqa: E402
from gemini_openai.gemini_pool import manager  # noqa: E402

SCRATCH = ("/tmp/claude-1000/-mnt-d-home-Projects-gemini-scraper/"
           "a5dc857e-7431-4552-865a-c41744e927e7/scratchpad")


async def main():
    t0 = time.time()
    print(f"u/{N}: generating video...", flush=True)
    try:
        res = await vmod.generate_video_url(
            manager, "a golden retriever puppy running on a beach at sunset, cinematic",
            timeout=260,
        )
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ generate failed: {type(e).__name__}: {str(e)[:90]} ({int(time.time()-t0)}s)", flush=True)
        await manager.reset()
        return
    url = res["download_url"]
    print(f"  ✅ got URL in {int(time.time()-t0)}s: {url[:90]}...", flush=True)
    with open(f"{SCRATCH}/vidurl_fresh.txt", "w") as f:
        f.write(url)

    # Try immediate server-side download
    os.makedirs("media", exist_ok=True)
    try:
        size = await vmod.download_video(manager, url, "media/fresh_test.mp4")
        print(f"  🎬 SERVER-SIDE DOWNLOAD WORKS: {size} bytes -> media/fresh_test.mp4", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ immediate server-side download: {type(e).__name__}: {str(e)[:90]}", flush=True)
        print("     -> 403 on a FRESH url means it's OSID auth, not expiry.", flush=True)
    await manager.reset()


asyncio.run(main())
