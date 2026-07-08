"""Raw video request on thmanyah (u/5) with the video tool flag; dump response.

Reuses the proven full-jar + /u/5/ auth from u5_fulljar_probe, but adds the
reverse-engineered video tool flag at message_content[9] and prints the raw
response so we can see how the server delivers the video (URL / CID / async).
"""

import asyncio
import json
import re
import urllib.parse

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
    sess = AsyncSession(impersonate="chrome", allow_redirects=True)
    for k, v in jar.items():
        sess.cookies.set(k, v, domain=".google.com")
    r = await sess.get(f"{BASE}/app", headers={"Referer": "https://gemini.google.com/"})
    at = re.search(r'"SNlM0e":\s*"(.*?)"', r.text).group(1)
    bl = re.search(r'"cfb2h":\s*"(.*?)"', r.text).group(1)
    sidm = re.search(r'"FdrFJe":\s*"(.*?)"', r.text)

    # message_content WITH the video tool flag at index 9
    mc = ["A red fox running through a snowy forest at sunrise, cinematic", 0, None, None, None, None, 0, None, None, VIDEO_TOOL]
    inner = [None] * 69
    inner[0] = mc
    inner[1] = ["en"]
    inner[2] = ["", "", ""]
    inner[17] = [[1]]   # video mode (vs [[0]] image)
    inner[54] = []
    inner[55] = [[16]]  # 16:9 aspect
    freq = json.dumps([None, json.dumps(inner)])
    params = {"bl": bl, "rt": "c", "_reqid": "200000", "hl": "en"}
    if sidm:
        params["f.sid"] = sidm.group(1)
    url = f"{BASE}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate?" + urllib.parse.urlencode(params)
    VIDEO_HDR = ('[1,null,null,null,"e6fa609c3fa255c0",null,null,0,[4,5,6,8],'
                 'null,null,2,null,null,3,null,"BD304575-CA78-4C84-A52C-FA37084DEF9A"]')
    r2 = await sess.post(
        url,
        data={"at": at, "f.req": freq},
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Referer": "https://gemini.google.com/", "Origin": "https://gemini.google.com",
            "X-Same-Domain": "1",
            "x-goog-ext-525001261-jspb": VIDEO_HDR,
        },
    )
    print("StreamGenerate ->", r2.status_code, "len:", len(r2.text))
    txt = r2.text
    for kw in ("video", ".mp4", "googlevideo", "veo", "generating", "pending", "\"68\"", "data_analysis"):
        idx = txt.lower().find(kw.lower())
        print(f"  marker {kw!r}: at {idx}")
    # save full response for inspection
    with open("scratch_video_resp.txt", "w") as f:
        f.write(txt)
    print("=== full response saved to scratch_video_resp.txt ===")
    await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
