"""Complete raw video request with a long read — capture how the video is delivered.

Replicates gemini_webapi's full inner_req_list fields plus the reverse-engineered
video flags, sends to /u/6 with the video model header, and reads the streaming
response with a long timeout to see whether the video URL arrives in-band.
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

    import secrets
    mc = ["a butterfly landing on a flower, macro", 0, None, None, None, None, 0, None, None, VIDEO_TOOL]
    inner = [None] * 92
    inner[0] = mc
    inner[1] = ["en"]
    inner[2] = ["", "", ""]
    inner[3] = "!" + secrets.token_urlsafe(2600)   # async-op nonce (video needs it)
    inner[4] = uuid.uuid4().hex
    inner[6] = [1]
    inner[7] = 1
    inner[10] = 1
    inner[11] = 0
    inner[17] = [[1]]
    inner[18] = 0
    inner[27] = 1
    inner[30] = [4]
    inner[49] = 11
    inner[53] = 0
    inner[54] = []
    inner[55] = [[16]]
    inner[59] = uid
    inner[61] = []
    inner[68] = 2
    inner[79] = 3
    inner[91] = 0
    freq = json.dumps([None, json.dumps(inner)])
    params = {"bl": bl, "rt": "c", "_reqid": "300000", "hl": "en"}
    if sidm:
        params["f.sid"] = sidm.group(1)
    url = f"{BASE}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate?" + urllib.parse.urlencode(params)
    VIDEO_HDR = ('[1,null,null,null,"e6fa609c3fa255c0",null,null,0,[4,5,6,8],'
                 f'null,null,2,null,null,3,null,"{uid}"]')
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Referer": "https://gemini.google.com/", "Origin": "https://gemini.google.com",
        "X-Same-Domain": "1",
        "x-goog-ext-525001261-jspb": VIDEO_HDR,
        "x-goog-ext-525005358-jspb": f'["{uid}",1]',
    }
    t0 = time.time()
    print("POSTing video request (long read up to 300s)...", flush=True)
    r2 = await sess.post(url, data={"at": at, "f.req": freq}, headers=headers)
    dt = time.time() - t0
    txt = r2.text
    print(f"response in {dt:.0f}s, status {r2.status_code}, len {len(txt)}", flush=True)
    # search for video URLs
    urls = re.findall(r'https://[^"\\\s]+', txt)
    vids = [u for u in urls if ("video" in u.lower() or ".mp4" in u.lower() or "googleusercontent" in u.lower())]
    print("candidate media URLs:", flush=True)
    for u in sorted(set(vids))[:15]:
        print("  ", u[:130], flush=True)
    for kw in ["Creating your image", "Creating your video", "video", "veo", "mp4"]:
        print(f"  has {kw!r}: {kw.lower() in txt.lower()}", flush=True)
    with open("scratch_vstream.txt", "w") as f:
        f.write(txt)
    await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
