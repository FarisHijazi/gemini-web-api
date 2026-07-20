"""Cheap Veo-quota probe across Google profiles (u/N).

Generating a video to test quota costs up to 600s per exhausted account. Instead
ask Gemini *in chat* to create a video: an exhausted account answers with a
"come back tomorrow"-style refusal in seconds, while an account with quota starts
generating. Also confirms the profile is signed in at all.
"""

import asyncio
import shutil
import sys

import gemini_openai.config as config
from gemini_openai.account import apply_authuser
from gemini_openai.gemini_pool import manager

QUOTA_HINTS = (
    "come back tomorrow", "can't generate more videos", "can't create more videos",
    "cannot generate more videos", "daily limit", "limit resets", "try again tomorrow",
    "reached your limit", "out of", "quota",
)
PROFILES = [int(a) for a in sys.argv[1:]] or [0, 1, 2, 3, 4, 5, 6]


async def switch(n: int):
    await manager.reset()
    shutil.rmtree("/tmp/gemini_webapi", ignore_errors=True)
    config.AUTHUSER = str(n)
    apply_authuser(str(n))


async def probe(n: int) -> str:
    await switch(n)
    try:
        client = await asyncio.wait_for(manager.get(), timeout=45)
    except Exception as e:  # noqa: BLE001
        return f"u/{n}: NOT USABLE ({type(e).__name__})"
    # 1. signed in?
    try:
        out = await asyncio.wait_for(client.generate_content("Reply with just: OK"), timeout=45)
        if not (out and out.text):
            return f"u/{n}: no chat response"
    except Exception as e:  # noqa: BLE001
        return f"u/{n}: chat failed ({type(e).__name__}: {str(e)[:50]})"
    # 2. ask for a video and read the refusal (cheap quota signal)
    try:
        out = await asyncio.wait_for(
            client.generate_content("Create a short video of a cat walking in a garden."),
            timeout=90,
        )
        txt = (out.text or "").strip()
    except Exception as e:  # noqa: BLE001
        return f"u/{n}: signed in, video-ask failed ({type(e).__name__}: {str(e)[:50]})"
    low = txt.lower()
    hit = next((h for h in QUOTA_HINTS if h in low), None)
    verdict = "QUOTA EXHAUSTED" if hit else "possible QUOTA AVAILABLE"
    return f"u/{n}: signed in | {verdict}" + (f" [matched '{hit}']" if hit else "") + \
           f"\n      reply: {txt[:150]!r}"


async def main():
    for n in PROFILES:
        print(await probe(n), flush=True)
    await manager.reset()


asyncio.run(main())
