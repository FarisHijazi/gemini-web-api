"""Send the raw video request over the library's INITIALIZED session.

The same fields get 1053 from a fresh session but are accepted by the library's
session (which has the extra cookies set during init's batchexecute calls). So
we let the library init, then reuse client.client + client.access_token to send
our own StreamGenerate and dump the accepted response — then we control polling.
"""

import asyncio
import json
import os
import re
import time
import urllib.parse
import uuid

os.environ["GEMINI_AUTHUSER"] = "6"
from gemini_openai import account, config, video as vmod  # noqa: E402
from gemini_webapi import GeminiClient  # noqa: E402

BASE = "https://gemini.google.com/u/6"
VIDEO_TOOL = [None, None, None, None, None, None, [[None, None, None, 1]]]
DEFAULT_METADATA = ["", "", "", None, None, None, None, None, None, ""]


def build_inner(prompt, uid):
    mc = [prompt, 0, None, None, None, None, 0, None, None, VIDEO_TOOL]
    inner = [None] * 69
    inner[0] = mc; inner[1] = ["en"]; inner[2] = DEFAULT_METADATA
    inner[6] = [1]; inner[7] = 1; inner[10] = 1; inner[11] = 0
    inner[17] = [[1]]; inner[18] = 0; inner[27] = 1; inner[30] = [4]; inner[41] = [1]
    inner[49] = 11; inner[53] = 0; inner[54] = []; inner[55] = [[16]]
    inner[59] = uid; inner[61] = []; inner[68] = 2
    return inner


async def main():
    account.apply_authuser("6")
    account.install_full_jar(config.get_full_jar)
    psid, psidts = config.get_cookies()
    c = GeminiClient(psid, psidts)
    await c.init(timeout=40, auto_refresh=False)
    print("init ok; access_token len", len(c.access_token or ""), flush=True)

    sess = c.client
    at = c.access_token
    bl = c.build_label
    uid = str(uuid.uuid4()).upper()
    inner = build_inner("a single red balloon floating in a clear blue sky", uid)
    freq = json.dumps([None, json.dumps(inner)])
    params = {"hl": "en", "_reqid": "500123", "rt": "c", "bl": bl}
    if getattr(c, "session_id", None):
        params["f.sid"] = c.session_id
    url = f"{BASE}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate?" + urllib.parse.urlencode(params)
    video_hdr = ('[1,null,null,null,"e6fa609c3fa255c0",null,null,0,[4,5,6,8],'
                 'null,null,2,null,null,3,null,"00000000-0000-0000-0000-000000000000"]')
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        "Origin": "https://gemini.google.com", "Referer": "https://gemini.google.com/",
        "X-Same-Domain": "1",
        "x-goog-ext-525001261-jspb": video_hdr,
        "x-goog-ext-525005358-jspb": f'["{uid}",1]',
    }
    t0 = time.time()
    print("POST via library session...", flush=True)
    r = await sess.post(url, data={"at": at, "f.req": freq}, headers=headers)
    txt = r.text
    print(f"status {r.status_code} len {len(txt)} in {time.time()-t0:.0f}s", flush=True)
    err = re.search(r"BardErrorInfo\D+(\d+)", txt)
    print("ERROR CODE:", err.group(1) if err else "NONE (accepted!)", flush=True)
    cid = re.search(r"c_[0-9a-f]{16}", txt)
    rid = re.search(r"r_[0-9a-f]{16}", txt)
    rcid = re.search(r"rc_[0-9a-f]{16}", txt)
    print("CID:", cid.group(0) if cid else None, "RID:", rid.group(0) if rid else None,
          "RCID:", rcid.group(0) if rcid else None, flush=True)
    for kw in ["Creating your video", "Creating your image"]:
        print(f"  {kw!r}: {kw in txt}", flush=True)
    with open("scratch_vlibsess.txt", "w") as f:
        f.write(txt)
    print("saved scratch_vlibsess.txt", flush=True)
    await c.close()


asyncio.run(main())
