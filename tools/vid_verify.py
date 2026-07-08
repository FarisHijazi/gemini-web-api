import asyncio
import os

import gemini_webapi.client as gc

os.environ["GEMINI_AUTHUSER"] = "6"
from gemini_openai import account, config, video as vmod  # noqa: E402
from gemini_webapi import GeminiClient  # noqa: E402

# capture what actually gets serialized for the generate payload
captured = {}
_proxy = gc.json
_orig_dumps = _proxy.dumps  # proxy's own dumps (does the injection)


def wrapped_dumps(obj, *a, **k):
    s = _orig_dumps(obj, *a, **k)  # runs injection + real serialize
    if isinstance(obj, list) and len(obj) == 69 and isinstance(obj[0], list):
        captured["mc_len"] = len(obj[0])
        captured["mc9"] = obj[0][9] if len(obj[0]) > 9 else "ABSENT"
        captured["idx17"] = obj[17]
        captured["idx54"] = obj[54]
        captured["idx55"] = obj[55]
    return s


_proxy.dumps = wrapped_dumps


async def main():
    account.apply_authuser("6")
    account.install_full_jar(config.get_full_jar)
    psid, psidts = config.get_cookies()
    c = GeminiClient(psid, psidts)
    await c.init(timeout=40, auto_refresh=False)
    print("init ok", flush=True)
    tok = vmod._video_ctx.set(True)
    try:
        out = await asyncio.wait_for(
            c.generate_content(
                "a calm ocean wave at sunset",
                model={"model_name": "gemini-3-video", "model_header": dict(vmod.VIDEO_MODEL["model_header"])},
            ),
            timeout=90,
        )
        print("videos:", len(out.videos or []), "images:", len(out.images or []), flush=True)
    except Exception as e:  # noqa: BLE001
        print("ERR:", type(e).__name__, str(e)[:120], flush=True)
    finally:
        vmod._video_ctx.reset(tok)
    print("CAPTURED SENT PAYLOAD:", captured, flush=True)
    await c.close()


asyncio.run(main())
