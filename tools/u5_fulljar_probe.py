"""Decisive test: full google.com cookie jar + /u/5/ path -> thmanyah account?

Replicates what the browser does for a non-default account: send the entire
auth cookie jar and hit the /u/5/ endpoints. If StreamGenerate returns a normal
answer, multi-account works via the full jar and we can wire it into the client.
"""

import asyncio
import json
import re
import urllib.parse

import browser_cookie3
from curl_cffi.requests import AsyncSession

STORE = "/home/faris/.config/google-chrome/Default/Cookies"
U = "5"
BASE = f"https://gemini.google.com/u/{U}"


def load_jar():
    cj = browser_cookie3.chrome(cookie_file=STORE, domain_name="google.com")
    jar = {}
    for c in cj:
        # prefer .google.com domain entries
        if c.domain.endswith(".google.com") and c.path == "/":
            jar[c.name] = c.value
    return jar


async def main():
    jar = load_jar()
    print("loaded", len(jar), "cookies; has 1PSID:", "__Secure-1PSID" in jar, "SAPISID:", "SAPISID" in jar)
    sess = AsyncSession(impersonate="chrome", allow_redirects=True)
    for k, v in jar.items():
        sess.cookies.set(k, v, domain=".google.com")

    r = await sess.get(f"{BASE}/app", headers={"Referer": "https://gemini.google.com/"})
    print("GET /u/5/app ->", r.status_code, "final url:", str(r.url)[:60])
    at = re.search(r'"SNlM0e":\s*"(.*?)"', r.text)
    bl = re.search(r'"cfb2h":\s*"(.*?)"', r.text)
    sid = re.search(r'"FdrFJe":\s*"(.*?)"', r.text)
    print("SNlM0e found:", bool(at), "bl:", bl.group(1) if bl else None)
    if not at:
        print("no token -> account not authenticated at u/5 via jar")
        await sess.close()
        return

    inner = [["Reply with exactly: THMANYAH_JAR_OK", 0, None, None, None, None, 0], ["en"], ["", "", ""]]
    inner_list = [None] * 69
    inner_list[0] = inner[0]
    inner_list[1] = inner[1]
    inner_list[2] = inner[2]
    freq = json.dumps([None, json.dumps(inner_list)])
    data = {"at": at.group(1), "f.req": freq}
    params = {"bl": bl.group(1) if bl else "", "rt": "c", "_reqid": "100000", "hl": "en"}
    if sid:
        params["f.sid"] = sid.group(1)
    url = f"{BASE}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate?" + urllib.parse.urlencode(params)
    r2 = await sess.post(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                 "Referer": "https://gemini.google.com/", "Origin": "https://gemini.google.com",
                 "X-Same-Domain": "1"},
    )
    print("POST StreamGenerate ->", r2.status_code, "len:", len(r2.text))
    # crude: does response contain our marker?
    print("contains marker:", "THMANYAH_JAR_OK" in r2.text)
    print("resp head:", repr(r2.text[:200]))
    await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
