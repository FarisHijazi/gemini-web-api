"""Raw video request matching gemini_webapi's EXACT field set + video flags.

Earlier raw attempts added deep-research nonces (inner[3]/[4]) and got error 1053.
The library's own generate_content (which sets the fields below and NO nonces)
was accepted (async pending). This replicates that exact field set so the request
is accepted, then dumps the response so we can see the CID and how the finished
video is delivered (in-band later chunk vs a separate poll RPC).
"""

import asyncio
import json
import re
import time
import urllib.parse
import uuid

import browser_cookie3
from curl_cffi.requests import AsyncSession

STORE = "/home/faris/.config/google-chrome/Default/Cookies"
BASE = "https://gemini.google.com/u/6"
VIDEO_TOOL = [None, None, None, None, None, None, [[None, None, None, 1]]]
DEFAULT_METADATA = ["", "", "", None, None, None, None, None, None, ""]


def load_jar():
    cj = browser_cookie3.chrome(cookie_file=STORE, domain_name="google.com")
    return {c.name: c.value for c in cj if c.domain.endswith(".google.com") and c.path == "/"}


def build_inner(prompt, uid):
    mc = [prompt, 0, None, None, None, None, 0, None, None, VIDEO_TOOL]
    inner = [None] * 69
    inner[0] = mc
    inner[1] = ["en"]
    inner[2] = DEFAULT_METADATA
    inner[6] = [1]
    inner[7] = 1
    inner[10] = 1
    inner[11] = 0
    inner[17] = [[1]]      # video (library default [[0]] = image)
    inner[18] = 0
    inner[27] = 1
    inner[30] = [4]
    inner[41] = [1]
    inner[49] = 11         # video generation type
    inner[53] = 0
    inner[54] = []
    inner[55] = [[16]]     # 16:9
    inner[59] = uid
    inner[61] = []
    inner[68] = 2
    return inner


async def main():
    jar = load_jar()
    sess = AsyncSession(impersonate="chrome", allow_redirects=True, timeout=300)
    for k, v in jar.items():
        sess.cookies.set(k, v, domain=".google.com")
    r = await sess.get(f"{BASE}/app", headers={"Referer": "https://gemini.google.com/"})
    at = re.search(r'"SNlM0e":\s*"(.*?)"', r.text).group(1)
    bl = re.search(r'"cfb2h":\s*"(.*?)"', r.text).group(1)
    sidm = re.search(r'"FdrFJe":\s*"(.*?)"', r.text)
    uid = str(uuid.uuid4()).upper()

    inner = build_inner("a single red balloon floating in a clear blue sky", uid)
    freq = json.dumps([None, json.dumps(inner)])
    params = {"bl": bl, "rt": "c", "_reqid": "400000", "hl": "en"}
    if sidm:
        params["f.sid"] = sidm.group(1)
    url = f"{BASE}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate?" + urllib.parse.urlencode(params)
    # match the library exactly: zero uuid in the model header, no 73010989/90
    video_hdr = ('[1,null,null,null,"e6fa609c3fa255c0",null,null,0,[4,5,6,8],'
                 'null,null,2,null,null,3,null,"00000000-0000-0000-0000-000000000000"]')
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        "Referer": "https://gemini.google.com/", "Origin": "https://gemini.google.com",
        "X-Same-Domain": "1",
        "x-goog-ext-525001261-jspb": video_hdr,
        "x-goog-ext-525005358-jspb": f'["{uid}",1]',
    }
    t0 = time.time()
    print("POST video (library-exact fields, no nonces)...", flush=True)
    r2 = await sess.post(url, data={"at": at, "f.req": freq}, headers=headers)
    txt = r2.text
    print(f"status {r2.status_code} len {len(txt)} in {time.time()-t0:.0f}s", flush=True)
    err = re.search(r"BardErrorInfo\D+(\d+)", txt)
    print("ERROR CODE:", err.group(1) if err else "none", flush=True)
    cid = re.search(r"c_[0-9a-f]{16}", txt)
    rcid = re.search(r"rc_[0-9a-f]{16}", txt)
    print("CID:", cid.group(0) if cid else None, "RCID:", rcid.group(0) if rcid else None, flush=True)
    for kw in ["Creating your video", "Creating your image", ".mp4", "googleusercontent", "video"]:
        print(f"  {kw!r}: {kw.lower() in txt.lower()}", flush=True)
    with open("scratch_vraw2.txt", "w") as f:
        f.write(txt)
    await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
