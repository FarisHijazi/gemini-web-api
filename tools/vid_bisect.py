"""Bisect which inner field(s) flip a working image request into a 1053 for video."""

import asyncio
import json
import re
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


async def send(sess, at, bl, sid, fields, use_video_hdr):
    uid = str(uuid.uuid4()).upper()
    inner = [None] * 69
    inner[0] = ["a red balloon in a blue sky", 0, None, None, None, None, 0, None, None, VIDEO_TOOL]
    inner[1] = ["en"]
    inner[2] = DEFAULT_METADATA
    for k, v in fields.items():
        inner[k] = v
    if inner[59] is None:
        inner[59] = uid
    freq = json.dumps([None, json.dumps(inner)])
    params = {"hl": "en", "_reqid": str(600000 + int(uid[:4], 16) % 90000), "rt": "c", "bl": bl}
    if sid:
        params["f.sid"] = sid
    url = f"{BASE}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate?" + urllib.parse.urlencode(params)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        "Origin": "https://gemini.google.com", "Referer": "https://gemini.google.com/",
        "X-Same-Domain": "1", "x-goog-ext-525005358-jspb": f'["{uid}",1]',
    }
    if use_video_hdr:
        headers["x-goog-ext-525001261-jspb"] = VIDEO_HDR
    r = await sess.post(url, data={"at": at, "f.req": freq}, headers=headers)
    txt = r.text
    err = re.search(r"BardErrorInfo\D+(\d+)", txt)
    kind = "?"
    if "Creating your video" in txt:
        kind = "VIDEO"
    elif "Creating your image" in txt:
        kind = "image"
    return (err.group(1) if err else None), kind, len(txt)


async def main():
    jar = load_jar()
    sess = AsyncSession(impersonate="chrome", allow_redirects=True, timeout=120)
    for k, v in jar.items():
        sess.cookies.set(k, v, domain=".google.com")
    r = await sess.get(f"{BASE}/app", headers={"Referer": "https://gemini.google.com/"})
    at = re.search(r'"SNlM0e":\s*"(.*?)"', r.text).group(1)
    bl = re.search(r'"cfb2h":\s*"(.*?)"', r.text).group(1)
    sidm = re.search(r'"FdrFJe":\s*"(.*?)"', r.text)
    sid = sidm.group(1) if sidm else None

    base = {17: [[1]], 55: [[16]], 54: []}
    cases = [(f"49={n} +video-flags", {**base, 49: n}, True) for n in (1, 2, 3, 4, 5, 8, 10, 11, 12)]
    for name, fields, vhdr in cases:
        try:
            code, kind, ln = await send(sess, at, bl, sid, dict(fields), vhdr)
            print(f"{name:38} -> err={code or '-':5} kind={kind:6} len={ln}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"{name:38} -> EXC {type(e).__name__}: {str(e)[:50]}", flush=True)
        await asyncio.sleep(1.5)
    await sess.close()


asyncio.run(main())
