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
    blob = "\n".join(raw).replace("\\u003d", "=").replace("\\/", "/")
    # all distinct gg/ urls with their following bytes (to see thumb vs video)
    urls = []
    for m in re.finditer(r'https://lh3\.googleusercontent\.com/gg/[A-Za-z0-9_-]+', blob):
        u = m.group(0)
        after = blob[m.end():m.end() + 30]
        urls.append((u, after))
    seen = set()
    uniq = [(u, a) for u, a in urls if not (u in seen or seen.add(u))]
    print(f"{len(uniq)} distinct gg/ urls", flush=True)

    sess = client.client
    for i, (u, after) in enumerate(uniq):
        variants = {
            "plain": u,
            "=mm,22": u + "=mm,22",
            "=m22": u + "=m22",
            "=dv": u + "=dv",
        }
        print(f"\nURL[{i}] after={after!r}", flush=True)
        for name, vu in variants.items():
            try:
                r = await sess.get(vu, headers={"Referer": "https://gemini.google.com/"}, timeout=60)
                ct = r.headers.get("content-type", "")
                ismp4 = b"ftyp" in (r.content[:64] if r.status_code == 200 else b"")
                print(f"   {name:8} -> {r.status_code} {ct[:30]} mp4={ismp4} bytes={len(r.content)}", flush=True)
                if ismp4:
                    os.makedirs("media", exist_ok=True)
                    open(f"media/dl_{i}.mp4", "wb").write(r.content)
                    print(f"      SAVED media/dl_{i}.mp4", flush=True)
                    await manager.reset(); return
            except Exception as e:  # noqa: BLE001
                print(f"   {name:8} -> EXC {str(e)[:40]}", flush=True)
    await manager.reset()


asyncio.run(main())
