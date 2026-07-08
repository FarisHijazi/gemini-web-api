"""Run the full video flow and capture every raw HTTP response to find the URL."""

import asyncio
import os
import re

os.environ["GEMINI_AUTHUSER"] = "6"
from gemini_openai import account, config, video as vmod  # noqa: E402
from gemini_openai.gemini_pool import manager  # noqa: E402

responses = []


async def main():
    client = await manager.get()
    sess = client.client
    orig_stream = sess.stream

    def stream(method, url, **kw):
        cm = orig_stream(method, url, **kw)
        return _Tee(cm)

    class _Tee:
        def __init__(self, cm):
            self.cm = cm
            self.resp = None

        async def __aenter__(self):
            self.resp = await self.cm.__aenter__()
            return _RespTee(self.resp)

        async def __aexit__(self, *a):
            return await self.cm.__aexit__(*a)

    class _RespTee:
        def __init__(self, resp):
            self._resp = resp
            self.status_code = resp.status_code

        async def aiter_content(self, *a, **k):
            buf = []
            async for chunk in self._resp.aiter_content(*a, **k):
                buf.append(chunk)
                yield chunk
            try:
                responses.append(b"".join(buf).decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001
                pass

        def __getattr__(self, n):
            return getattr(self._resp, n)

    sess.stream = stream

    out = await vmod.generate_video(manager, "a red fox running through a snowy forest at sunrise")
    print("text:", repr((out.text or "")[:80]), "videos:", len(out.videos or []), flush=True)
    print(f"captured {len(responses)} responses", flush=True)
    # search all responses for video urls
    all_txt = "\n\n=====\n\n".join(responses)
    with open("scratch_allresp.txt", "w") as f:
        f.write(all_txt)
    urls = sorted(set(re.findall(r'https?:\\?/\\?/[^"\\\s\]]+', all_txt)))
    media = [u.replace("\\/", "/") for u in urls if any(x in u.lower() for x in ("video", ".mp4", "googleusercontent", "lh3.", "veo", "generativelanguage"))]
    print("MEDIA URLS:", flush=True)
    for u in sorted(set(media)):
        print("  ", u[:150], flush=True)
    await manager.reset()


asyncio.run(main())
