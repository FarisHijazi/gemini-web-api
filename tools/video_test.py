"""Prove Veo video generation works via the injected tool flag."""

import asyncio
import time

from gemini_openai.gemini_pool import manager
from gemini_openai.video import generate_video


async def main() -> None:
    t0 = time.time()
    print("generating video (this can take 1-3 min)...", flush=True)
    out = await generate_video(
        manager,
        "A red fox running through a snowy forest at sunrise, cinematic, 8 seconds",
    )
    dt = time.time() - t0
    print(f"returned in {dt:.0f}s", flush=True)
    print("text:", repr((out.text or "")[:200]), flush=True)
    vids = out.videos or []
    print("num videos:", len(vids), flush=True)
    for i, v in enumerate(vids):
        print(f"  video[{i}] url={(v.url or '')[:90]!r} thumb={bool(getattr(v,'thumbnail',None))}", flush=True)
    if vids:
        print("saving (polls until ready)...", flush=True)
        path = await vids[0].save(path="tmp_videos", verbose=True)
        print("SAVED:", path, flush=True)
    await manager.reset()


if __name__ == "__main__":
    asyncio.run(main())
