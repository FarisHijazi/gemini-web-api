"""Test whether video generation needs an existing conversation context.

Prime a chat (get cid/rid/rcid via the library), then send the raw video request
with inner[2]=chat metadata, over the library's session. See if it yields video.
"""

import asyncio
import json
import os
import re
import time
import urllib.parse
import uuid

os.environ["GEMINI_AUTHUSER"] = "6"
from gemini_openai import account, config  # noqa: E402
from gemini_webapi import GeminiClient  # noqa: E402

BASE = "https://gemini.google.com/u/6"
VIDEO_TOOL = [None, None, None, None, None, None, [[None, None, None, 1]]]
VIDEO_HDR = ('[1,null,null,null,"e6fa609c3fa255c0",null,null,0,[4,5,6,8],'
             'null,null,2,null,null,3,null,"00000000-0000-0000-0000-000000000000"]')


async def send_video(sess, at, bl, sid, metadata, prompt, extra49=None):
    uid = str(uuid.uuid4()).upper()
    inner = [None] * 69
    inner[0] = [prompt, 0, None, None, None, None, 0, None, None, VIDEO_TOOL]
    inner[1] = ["en"]
    inner[2] = metadata
    inner[17] = [[1]]; inner[54] = []; inner[55] = [[16]]
    if extra49 is not None:
        inner[49] = extra49
    inner[59] = uid
    freq = json.dumps([None, json.dumps(inner)])
    params = {"hl": "en", "_reqid": str(800000 + int(uid[:4], 16) % 90000), "rt": "c", "bl": bl}
    if sid:
        params["f.sid"] = sid
    url = f"{BASE}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate?" + urllib.parse.urlencode(params)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        "Origin": "https://gemini.google.com", "Referer": "https://gemini.google.com/",
        "X-Same-Domain": "1", "x-goog-ext-525001261-jspb": VIDEO_HDR,
        "x-goog-ext-525005358-jspb": f'["{uid}",1]',
    }
    t0 = time.time()
    r = await sess.post(url, data={"at": at, "f.req": freq}, headers=headers, timeout=300)
    txt = r.text
    err = re.search(r"BardErrorInfo\D+(\d+)", txt)
    kind = "video" if "Creating your video" in txt else ("image" if "Creating your image" in txt else "?")
    print(f"    -> {time.time()-t0:.0f}s err={err.group(1) if err else '-'} kind={kind} len={len(txt)}", flush=True)
    return txt


async def main():
    account.apply_authuser("6")
    account.install_full_jar(config.get_full_jar)
    psid, psidts = config.get_cookies()
    c = GeminiClient(psid, psidts)
    await c.init(timeout=40, auto_refresh=False)
    print("init ok", flush=True)

    # 1) prime a conversation with a normal text turn
    chat = c.start_chat()
    out = await chat.send_message("Let's make some videos.")
    meta = chat.metadata
    print("primed chat; metadata:", json.dumps(meta)[:80], flush=True)

    sess = c.client
    at, bl, sid = c.access_token, c.build_label, getattr(c, "session_id", None)

    print("A) video with conversation metadata, 300s timeout:", flush=True)
    txt = await send_video(sess, at, bl, sid, meta, "a red fox running through snowy forest at sunrise")
    with open("scratch_vctx.txt", "w") as f:
        f.write(txt)
    # find video-ish URLs
    urls = sorted(set(re.findall(r'https?:\\?/\\?/[^"\\\s\]]+', txt)))
    media = [u for u in urls if any(x in u.lower() for x in ("video", ".mp4", "googleusercontent", "lh3.", "veo"))]
    print("       media urls:", len(media), flush=True)
    for u in media[:10]:
        print("         ", u[:130], flush=True)
    for kw in [".mp4", "video/mp4", "Creating your video", "Creating your image", "generated_video"]:
        print(f"       {kw!r}: {kw.lower() in txt.lower()}", flush=True)
    await c.close()


asyncio.run(main())
