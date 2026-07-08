"""Capture the EXACT HTTP request gemini_webapi sends for a video generation,
so we can diff it against our raw request and find the 1053 cause."""

import asyncio
import json
import os

os.environ["GEMINI_AUTHUSER"] = "6"
from gemini_openai import account, config, video as vmod  # noqa: E402
from gemini_webapi import GeminiClient  # noqa: E402
import gemini_webapi.client as gc  # noqa: E402

captured = {}


async def main():
    account.apply_authuser("6")
    account.install_full_jar(config.get_full_jar)
    psid, psidts = config.get_cookies()
    c = GeminiClient(psid, psidts)
    await c.init(timeout=40, auto_refresh=False)
    print("init ok", flush=True)

    # patch the live session's stream to capture the outgoing request
    sess = c.client
    orig_stream = sess.stream

    def stream(method, url, **kw):
        if "StreamGenerate" in str(url):
            captured["method"] = method
            captured["url"] = str(url)
            captured["params"] = kw.get("params")
            captured["headers"] = dict(kw.get("headers") or {})
            data = kw.get("data") or {}
            freq = data.get("f.req", "")
            captured["freq_raw"] = freq
            try:
                outer = json.loads(freq)
                inner = json.loads(outer[1])
                captured["inner_nonnull"] = {i: v for i, v in enumerate(inner) if v is not None}
            except Exception as e:  # noqa: BLE001
                captured["parse_err"] = str(e)
        return orig_stream(method, url, **kw)

    sess.stream = stream

    tok = vmod._video_ctx.set(True)
    atok = vmod._video_ctx_aspect.set(16)
    try:
        model = {"model_name": "gemini-3-video", "model_header": dict(vmod.VIDEO_MODEL["model_header"])}
        await asyncio.wait_for(c.generate_content("a single red balloon in a blue sky", model=model), timeout=30)
    except Exception as e:  # noqa: BLE001
        print("gen result:", type(e).__name__, str(e)[:80], flush=True)
    finally:
        vmod._video_ctx.reset(tok)
        vmod._video_ctx_aspect.reset(atok)

    # dump captured request (redact token-ish header values)
    hdrs = {}
    for k, v in captured.get("headers", {}).items():
        hdrs[k] = v if ("jspb" in k or k in ("Content-Type", "X-Same-Domain")) else f"<len {len(str(v))}>"
    print("URL:", captured.get("url"))
    print("PARAMS:", captured.get("params"))
    print("HEADERS:", json.dumps(hdrs, indent=1))
    print("INNER non-null indices:", sorted(captured.get("inner_nonnull", {}).keys()))
    nn = captured.get("inner_nonnull", {})
    for i in sorted(nn):
        s = json.dumps(nn[i])
        print(f"  [{i}] = {s[:100]}")
    await c.close()


asyncio.run(main())
