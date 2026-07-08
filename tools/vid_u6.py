import asyncio
import os
import time

os.environ["GEMINI_AUTHUSER"] = "6"
from gemini_openai.gemini_pool import manager  # noqa: E402
from gemini_openai.video import generate_video  # noqa: E402


async def main():
    t0 = time.time()
    print("generating video on u/6...", flush=True)
    out = await asyncio.wait_for(
        generate_video(manager, "A red fox running through a snowy forest at sunrise, cinematic"),
        timeout=480,
    )
    print(f"generate returned in {time.time()-t0:.0f}s", flush=True)
    print("text:", repr((out.text or "")[:200]), flush=True)
    vids = out.videos or []
    print("num videos:", len(vids), "num images:", len(out.images or []), flush=True)
    for i, v in enumerate(vids):
        print(f"  video[{i}].url head:", (v.url or "")[:80], flush=True)
    if vids:
        p = await asyncio.wait_for(vids[0].save(path="media", filename="fox_u6.mp4", verbose=True), timeout=480)
        print("SAVED:", p, flush=True)
        print("size:", os.path.getsize(p) if os.path.isfile(p) else "N/A", flush=True)
    await manager.reset()


asyncio.run(main())
