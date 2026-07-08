"""Full video flow: prime → send video → poll read_chat raw for the mp4 URL."""

import asyncio
import os
import re
import time

os.environ["GEMINI_AUTHUSER"] = "6"
from gemini_openai import video as vmod  # noqa: E402
from gemini_openai.gemini_pool import manager  # noqa: E402
from gemini_webapi.constants import Model  # noqa: E402
from gemini_webapi.exceptions import APIError  # noqa: E402

raw_responses = []


async def main():
    client = await manager.get()

    # capture raw batch_execute responses (read_chat uses it)
    orig_be = client._batch_execute

    async def cap_be(payloads, *a, **k):
        resp = await orig_be(payloads, *a, **k)
        try:
            raw_responses.append(resp.text)
        except Exception:  # noqa: BLE001
            pass
        return resp

    client._batch_execute = cap_be

    # 1) prime a conversation
    chat = client.start_chat(model=Model.BASIC_PRO)
    await chat.send_message("I want to create a video. Reply with just: READY")
    cid = chat.cid
    print("primed; cid:", cid, flush=True)

    # 2) send the video request; the async-pending state raises — that's fine,
    #    generation is now running in this conversation.
    tok = vmod._video_ctx.set(True)
    atok = vmod._video_ctx_aspect.set(16)
    try:
        out = await asyncio.wait_for(
            chat.send_message("a yellow submarine underwater surrounded by colorful fish"),
            timeout=45,
        )
        print("video turn returned; text:", repr((out.text or "")[:60]), "videos:", len(out.videos or []), flush=True)
    except (APIError, asyncio.TimeoutError) as e:
        print("video turn pending/err (expected):", type(e).__name__, flush=True)
    finally:
        vmod._video_ctx.reset(tok)
        vmod._video_ctx_aspect.reset(atok)

    # 3) poll read_chat until a real mp4/video URL appears
    VID_RE = re.compile(r'https://[^"\\\s\]]*?(?:gg-dl|googleusercontent|videoplayback|\.mp4)[^"\\\s\]]*')
    found = None
    t0 = time.time()
    for i in range(40):
        raw_responses.clear()
        try:
            await client.read_chat(cid, limit=3)
        except Exception as e:  # noqa: BLE001
            print("read_chat err:", e, flush=True)
        blob = "\n".join(raw_responses)
        cands = [u.replace("\\/", "/") for u in VID_RE.findall(blob)]
        # exclude thumbnails / image gen; keep video-ish
        vids = [u for u in cands if "video" in u.lower() or ".mp4" in u.lower() or "gg-dl" in u.lower()]
        has_chip = "video_gen_chip" in blob
        print(f"  poll {i} t+{time.time()-t0:.0f}s chip={has_chip} cand={len(set(cands))}", flush=True)
        if vids:
            found = sorted(set(vids), key=len, reverse=True)[0]
            break
        await asyncio.sleep(8)

    print("\nFOUND VIDEO URL:", found[:160] if found else None, flush=True)
    if found:
        with open("scratch_found_url.txt", "w") as f:
            f.write(found)
    await manager.reset()


asyncio.run(main())
