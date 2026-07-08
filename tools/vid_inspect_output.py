"""Generate a video and dump the full ModelOutput to locate the video URL."""

import asyncio
import json
import os
import re

os.environ["GEMINI_AUTHUSER"] = "6"
from gemini_openai import video as vmod  # noqa: E402
from gemini_openai.gemini_pool import manager  # noqa: E402


async def main():
    out = await vmod.generate_video(manager, "a red fox running through a snowy forest at sunrise")
    print("text:", repr((out.text or "")[:120]), flush=True)
    print("videos:", len(out.videos or []), "images:", len(out.images or []),
          "media:", len(getattr(out, "media", []) or []), flush=True)
    # dump the entire model as JSON and hunt for video urls
    try:
        dump = out.model_dump_json()
    except Exception:  # noqa: BLE001
        dump = json.dumps(str(out))
    with open("scratch_output.json", "w") as f:
        f.write(dump)
    print("dump len:", len(dump), flush=True)
    urls = sorted(set(re.findall(r'https?://[^"\\\s\]]+', dump)))
    media = [u for u in urls if any(x in u.lower() for x in ("video", ".mp4", "googleusercontent", "lh3.", "veo", "gg-dl"))]
    print("MEDIA-ish urls in output:", flush=True)
    for u in media[:15]:
        print("  ", u[:150], flush=True)
    # also inspect candidate structure keys
    cands = getattr(out, "candidates", None)
    if cands:
        c = cands[out.chosen]
        print("candidate fields:", list(c.model_dump().keys()) if hasattr(c, "model_dump") else dir(c), flush=True)
    await manager.reset()


asyncio.run(main())
