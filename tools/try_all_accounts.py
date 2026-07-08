"""Find a Google account (/u/N) with video quota and produce a real MP4."""

import asyncio
import os
import shutil
import time

import gemini_openai.config as config  # noqa: E402
from gemini_openai import video as vmod  # noqa: E402
from gemini_openai.account import apply_authuser  # noqa: E402
from gemini_openai.gemini_pool import manager  # noqa: E402


async def switch(n: int):
    await manager.reset()
    shutil.rmtree("/tmp/gemini_webapi", ignore_errors=True)
    config.AUTHUSER = str(n)
    apply_authuser(str(n))


async def is_valid_account(n: int) -> bool:
    """Init + a trivial chat to confirm /u/N is a signed-in account."""
    await switch(n)
    try:
        client = await asyncio.wait_for(manager.get(), timeout=40)
        out = await asyncio.wait_for(
            client.generate_content("Reply with just: OK"), timeout=40
        )
        return bool(out and out.text)
    except Exception as e:  # noqa: BLE001
        print(f"  u/{n}: not usable ({type(e).__name__})", flush=True)
        return False


async def try_video(n: int) -> bool:
    await switch(n)
    t0 = time.time()
    try:
        res = await vmod.generate_video_url(
            manager, "a red panda eating bamboo in a misty forest, cinematic", timeout=220
        )
        os.makedirs("media", exist_ok=True)
        path = f"media/account_u{n}.mp4"
        size = await vmod.download_video(manager, res["download_url"], path)
        print(f"  ✅ u/{n}: VIDEO DOWNLOADED {size} bytes -> {path} ({int(time.time()-t0)}s)", flush=True)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ u/{n}: {type(e).__name__}: {str(e)[:70]} ({int(time.time()-t0)}s)", flush=True)
        return False


async def main():
    print("=== Phase 1: discover valid /u/N accounts (0-9) ===", flush=True)
    valid = []
    for n in range(0, 10):
        if await is_valid_account(n):
            print(f"  u/{n}: VALID", flush=True)
            valid.append(n)
    print("valid indices:", valid, flush=True)

    print("\n=== Phase 2: try video on each valid account (skip known-exhausted 0,5,6 last) ===", flush=True)
    order = [n for n in valid if n not in (0, 5, 6)] + [n for n in valid if n in (0, 5, 6)]
    for n in order:
        if await try_video(n):
            print(f"\n🎬 SUCCESS on u/{n}", flush=True)
            break
    await manager.reset()


asyncio.run(main())
