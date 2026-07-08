"""Read an existing conversation that already has completed videos, to find the
video URL format (no new quota needed)."""

import asyncio
import os
import re

os.environ["GEMINI_AUTHUSER"] = "6"
from gemini_openai.gemini_pool import manager  # noqa: E402

CID = "c_41aa7f6f7f5c8276"  # the "Fox in Snowy Forest Video" conversation
raw = []


async def main():
    client = await manager.get()
    orig_be = client._batch_execute

    async def cap(payloads, *a, **k):
        r = await orig_be(payloads, *a, **k)
        try:
            raw.append(r.text)
        except Exception:  # noqa: BLE001
            pass
        return r

    client._batch_execute = cap

    hist = await client.read_chat(CID, limit=20)
    print("history turns:", len(hist.turns) if hist else 0, flush=True)
    if hist:
        for t in hist.turns:
            mo = getattr(t, "model_output", None)
            if mo:
                print(f"  turn role={t.role} videos={len(mo.videos or [])} media={len(getattr(mo,'media',[]) or [])} imgs={len(mo.images or [])}", flush=True)
                for v in (mo.videos or []):
                    print("    VIDEO.url:", (v.url or "")[:140], flush=True)
                for m in (getattr(mo, "media", []) or []):
                    print("    MEDIA.url:", (getattr(m, "url", "") or "")[:140], flush=True)

    blob = "\n".join(raw)
    with open("scratch_readchat.txt", "w") as f:
        f.write(blob)
    print("\nraw len:", len(blob), flush=True)
    # hunt for any mp4 / video URLs
    urls = sorted(set(u.replace("\\/", "/") for u in re.findall(r'https:\\?/\\?/[^"\\\s\]]+', blob)))
    for u in urls:
        if any(x in u.lower() for x in (".mp4", "video", "gg-dl", "googleusercontent", "videoplayback")):
            print("  URL:", u[:150], flush=True)
    await manager.reset()


asyncio.run(main())
