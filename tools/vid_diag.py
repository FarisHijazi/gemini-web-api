import asyncio
import os

import gemini_webapi.client as gc

os.environ["GEMINI_AUTHUSER"] = "5"
from gemini_openai import account, config, video as vmod  # noqa: E402
from gemini_webapi import GeminiClient  # noqa: E402

orig_dumps = gc.json.dumps
injected = {"count": 0}


def spy(obj, *a, **k):
    if (
        isinstance(obj, list)
        and len(obj) == 69
        and obj
        and isinstance(obj[0], list)
        and len(obj[0]) >= 10
        and obj[0][9] == vmod.VIDEO_TOOL
    ):
        injected["count"] += 1
    return orig_dumps(obj, *a, **k)


gc.json.dumps = spy


async def main():
    account.apply_authuser("5")
    account.install_full_jar(config.get_full_jar)
    psid, psidts = config.get_cookies()
    c = GeminiClient(psid, psidts)
    await c.init(timeout=40, auto_refresh=False)
    print("init ok", flush=True)
    tok = vmod._video_ctx.set(True)
    try:
        out = await asyncio.wait_for(
            c.generate_content("Make a video of a red fox in snow"), timeout=70
        )
        print("OUT text:", repr((out.text or "")[:150]), "videos:", len(out.videos or []), flush=True)
    except Exception as e:  # noqa: BLE001
        print("ERR:", type(e).__name__, str(e)[:150], flush=True)
    finally:
        vmod._video_ctx.reset(tok)
    print("INJECTION fired times:", injected["count"], flush=True)
    await c.close()


asyncio.run(main())
