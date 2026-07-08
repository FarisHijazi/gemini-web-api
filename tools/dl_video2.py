import asyncio
import os
import re

os.environ["GEMINI_AUTHUSER"] = "6"
from gemini_openai.gemini_pool import manager  # noqa: E402

CID = "c_41aa7f6f7f5c8276"
raw = []


async def main():
    client = await manager.get()
    orig = client._batch_execute

    async def cap(p, *a, **k):
        r = await orig(p, *a, **k)
        try:
            raw.append(r.text)
        except Exception:  # noqa: BLE001
            pass
        return r

    client._batch_execute = cap
    await client.read_chat(CID, limit=20)
    blob = "\n".join(raw)
    # decode any-depth \uXXXX escapes (batchexecute double-escapes) then slashes
    dec = re.sub(r'\\+u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), blob)
    dec = dec.replace("\\/", "/")
    dls = sorted(set(re.findall(r'https://[^"\\\s]*usercontent\.google\.com/download\?[^"\\\s]+', dec)))
    print("download urls:", len(dls), flush=True)
    sess = client.client
    for i, u in enumerate(dls):
        print(f"URL[{i}] len {len(u)}: ...{u[-60:]}", flush=True)
        r = await sess.get(u, headers={"Referer": "https://gemini.google.com/"}, timeout=120)
        ct = r.headers.get("content-type", "")
        ismp4 = b"ftyp" in r.content[:64]
        print(f"   -> {r.status_code} ct={ct[:30]} bytes={len(r.content)} mp4={ismp4}", flush=True)
        if ismp4 or (r.status_code == 200 and len(r.content) > 100000):
            os.makedirs("media", exist_ok=True)
            path = f"media/fox_video_{i}.mp4"
            open(path, "wb").write(r.content)
            print(f"   SAVED {path} ({len(r.content)} bytes)", flush=True)
    await manager.reset()


asyncio.run(main())
