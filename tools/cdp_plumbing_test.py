"""Validate the CDP plumbing in video_bridge (connect, createTarget, attach,
Runtime.evaluate, base64 roundtrip) against a throwaway Chrome — no Google auth.
"""

import asyncio
import base64
import json

import websockets

from gemini_openai import video_bridge as vb

CDP = "http://localhost:9333"

# Same-origin in-page fetch of the page's own bytes, base64'd — exercises the
# exact machinery fetch_video_bytes uses, minus the video-wait + Google auth.
JS = r"""
(async () => {
  const r = await fetch(location.href, { credentials: 'include' });
  const buf = new Uint8Array(await r.arrayBuffer());
  let s=''; const CH=0x8000;
  for (let i=0;i<buf.length;i+=CH) s += String.fromCharCode.apply(null, buf.subarray(i,i+CH));
  return JSON.stringify({ ok:true, status:r.status, b64:btoa(s), bytes:buf.length });
})()
"""


async def main():
    ws_url = vb._browser_ws_url(CDP)
    print("browser ws:", ws_url[:60], "...")
    async with websockets.connect(ws_url, max_size=None, open_timeout=10) as ws:
        cdp = vb._CDP(ws)
        try:
            res = await cdp.send("Target.createTarget", {"url": "https://example.com"})
            tid = res["targetId"]
            att = await cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
            sid = att["sessionId"]
            await cdp.send("Runtime.enable", session_id=sid)
            await asyncio.sleep(2)  # let example.com load
            out = await cdp.send("Runtime.evaluate",
                                 {"expression": JS, "awaitPromise": True, "returnByValue": True},
                                 session_id=sid, timeout=30)
            data = json.loads(out["result"]["value"])
            raw = base64.b64decode(data["b64"])
            print(f"  fetch status={data['status']} bytes={data['bytes']} decoded={len(raw)}")
            print("  roundtrip OK:", raw[:15])
            print("  contains <html>:", b"<html" in raw.lower() or b"<!doctype" in raw.lower())
            await cdp.send("Target.closeTarget", {"targetId": tid})
            print("PLUMBING OK ✅")
        finally:
            cdp.close()


asyncio.run(main())
