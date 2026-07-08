"""Send the no-49 video request and dump what kind of media comes back."""

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
VIDEO_HDR = ('[1,null,null,null,"e6fa609c3fa255c0",null,null,0,[4,5,6,8],'
             'null,null,2,null,null,3,null,"00000000-0000-0000-0000-000000000000"]')


def load_jar():
    cj = browser_cookie3.chrome(cookie_file=STORE, domain_name="google.com")
    return {c.name: c.value for c in cj if c.domain.endswith(".google.com") and c.path == "/"}


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

    inner = [None] * 69
    inner[0] = ["a red fox running through a snowy forest at sunrise, cinematic", 0, None, None, None, None, 0, None, None, VIDEO_TOOL]
    inner[1] = ["en"]; inner[2] = DEFAULT_METADATA
    inner[17] = [[1]]; inner[54] = []; inner[55] = [[16]]
    inner[59] = uid
    freq = json.dumps([None, json.dumps(inner)])
    params = {"hl": "en", "_reqid": "700001", "rt": "c", "bl": bl}
    if sidm:
        params["f.sid"] = sidm.group(1)
    url = f"{BASE}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate?" + urllib.parse.urlencode(params)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        "Origin": "https://gemini.google.com", "Referer": "https://gemini.google.com/",
        "X-Same-Domain": "1", "x-goog-ext-525001261-jspb": VIDEO_HDR,
        "x-goog-ext-525005358-jspb": f'["{uid}",1]',
    }
    t0 = time.time()
    print("POST (17+54+55+vhdr, no 49)...", flush=True)
    r2 = await sess.post(url, data={"at": at, "f.req": freq}, headers=headers)
    txt = r2.text
    print(f"status {r2.status_code} len {len(txt)} in {time.time()-t0:.0f}s", flush=True)
    err = re.search(r"BardErrorInfo\D+(\d+)", txt)
    print("err:", err.group(1) if err else "none", flush=True)
    # find all https urls and classify
    urls = sorted(set(re.findall(r'https?:\\?/\\?/[^"\\\s\]]+', txt)))
    media = [u for u in urls if any(x in u.lower() for x in ("video", ".mp4", "googleusercontent", "veo", "lh3."))]
    print(f"total urls {len(urls)}, media-ish {len(media)}:", flush=True)
    for u in media[:12]:
        print("   ", u[:120], flush=True)
    for kw in ["Creating your video", "Creating your image", "video/mp4", "image/png", "image/jpeg", ".mp4", "generated_video", "video_generation"]:
        print(f"  {kw!r}: {kw.lower() in txt.lower()}", flush=True)
    with open("scratch_vdump.txt", "w") as f:
        f.write(txt)
    print("saved scratch_vdump.txt", flush=True)
    await sess.close()


asyncio.run(main())
