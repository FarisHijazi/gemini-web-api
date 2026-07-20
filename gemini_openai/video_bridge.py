"""Browser bridge for downloading finished Veo videos.

Why this exists
---------------
Finished Veo videos are served from `*.usercontent.google.com/download?...`.
That host enforces a **per-host, per-account `OSID` cookie** that only the
browser mints (transparently, when the `<video>` element first loads). A plain
server-side GET with the full `.google.com` cookie jar gets a hard **403** — the
`gemini_webapi` library hits the exact same wall (it downloads from the same
`urls[1]` usercontent URL).

The one context where the bytes ARE reachable: a **credentialed `fetch()` issued
from inside a `gemini.google.com/u/N` page of the same account** — the same
origin/credentials context the `<video>` element uses. Google returns proper
CORS headers to `gemini.google.com`, so that fetch reads back real
`video/mp4` bytes (verified: 200, `video/mp4`, ~2.3 MB).

So we drive a logged-in Chrome over the DevTools Protocol (CDP):
  1. open a tab at the conversation URL (renders the video → mints the host OSID)
  2. `fetch(video_url, {credentials:'include'})` in-page → base64 the bytes
  3. hand the bytes back to the server, tagged by `job_id`

Parallel-safe by construction: each job runs in its own throwaway CDP tab and the
bytes are returned directly to the caller (never written to a shared Downloads
folder), so concurrent jobs can never be confused for one another — the `job_id`
that owns the tab is the single source of truth.

Enablement (opt-in): start Chrome once with a debug port and point the server at
it, e.g.

    google-chrome --remote-debugging-port=9222 \
        --user-data-dir="$HOME/.config/google-chrome"   # your logged-in profile
    GEMINI_CDP_URL=http://localhost:9222 uv run --active python main.py

When `GEMINI_CDP_URL` is unset the server skips the bridge and simply returns the
(browser-playable) `download_url` — no behaviour change.
"""

from __future__ import annotations

import asyncio
import base64
import json
import urllib.request

import websockets

# In-page routine: wait for the video element (which triggers the host OSID
# mint), then fetch the exact target URL with credentials and base64 the bytes.
# Returns {"ok":true,"b64":...} or {"ok":false,"status":n} — never the token.
_FETCH_JS = r"""
(async () => {
  const target = %s;
  const enc = (buf) => {
    let s = ''; const CH = 0x8000;
    for (let i = 0; i < buf.length; i += CH) s += String.fromCharCode.apply(null, buf.subarray(i, i + CH));
    return btoa(s);
  };
  const tryFetch = async () => {
    const r = await fetch(target, { credentials: 'include' });
    if (!r.ok) return { ok: false, status: r.status };
    const buf = new Uint8Array(await r.arrayBuffer());
    return { ok: true, b64: enc(buf), bytes: buf.length };
  };
  try {
    // First attempt — succeeds if the host OSID is already minted.
    let res = await tryFetch();
    if (res.ok) return JSON.stringify(res);
    // Not authorized yet: load the URL in a throwaway <video> to make the
    // browser mint the per-host OSID (the same thing the app's player does),
    // then retry the credentialed fetch.
    await new Promise((resolve) => {
      const v = document.createElement('video');
      v.style.display = 'none'; v.muted = true; v.src = target; v.crossOrigin = 'use-credentials';
      const done = () => { try { v.remove(); } catch (e) {} resolve(); };
      v.addEventListener('loadeddata', done, { once: true });
      v.addEventListener('error', () => setTimeout(done, 1500), { once: true });
      document.body.appendChild(v);
      setTimeout(done, 15000);  // hard cap
    });
    res = await tryFetch();
    return JSON.stringify(res);
  } catch (e) {
    return JSON.stringify({ ok: false, error: String(e).slice(0, 120) });
  }
})()
"""


class _CDP:
    """Minimal CDP client over a single browser-level websocket (flat sessions)."""

    def __init__(self, ws):
        self._ws = ws
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self):
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                fut = self._pending.pop(msg.get("id"), None)
                if fut and not fut.done():
                    fut.set_result(msg)
        except Exception:  # noqa: BLE001
            pass

    async def send(self, method: str, params: dict | None = None,
                   session_id: str | None = None, timeout: float = 30.0) -> dict:
        self._id += 1
        mid = self._id
        payload = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self._ws.send(json.dumps(payload))
        msg = await asyncio.wait_for(fut, timeout=timeout)
        if "error" in msg:
            raise RuntimeError(f"CDP {method} error: {msg['error']}")
        return msg.get("result", {})

    def close(self):
        self._reader.cancel()


def _browser_ws_url(cdp_http: str) -> str:
    with urllib.request.urlopen(cdp_http.rstrip("/") + "/json/version", timeout=5) as r:
        return json.load(r)["webSocketDebuggerUrl"]


async def fetch_cookies(cdp_http: str, domain_suffix: str = "google.com") -> dict[str, str]:
    """Harvest cookies (including httpOnly ones like __Secure-1PSID) over CDP.

    Page JavaScript cannot read httpOnly cookies, but the DevTools
    `Storage.getCookies` command returns the browser's full cookie store — so
    pointing at a logged-in Chrome removes any need to copy cookies by hand.
    """
    ws_url = await asyncio.get_event_loop().run_in_executor(None, _browser_ws_url, cdp_http)
    async with websockets.connect(ws_url, max_size=None, open_timeout=10) as ws:
        cdp = _CDP(ws)
        try:
            res = await cdp.send("Storage.getCookies", {})
            out: dict[str, str] = {}
            for c in res.get("cookies", []):
                if domain_suffix in (c.get("domain") or ""):
                    out[c["name"]] = c["value"]
            return out
        finally:
            cdp.close()


async def fetch_video_bytes(cdp_http: str, page_url: str, video_url: str,
                            timeout: float = 90.0) -> bytes:
    """Download a finished-video URL through a logged-in Chrome via CDP.

    page_url  : the gemini.google.com/u/N/app/<cid> conversation (renders video)
    video_url : the usercontent download URL to fetch in-page
    """
    ws_url = await asyncio.get_event_loop().run_in_executor(None, _browser_ws_url, cdp_http)
    async with websockets.connect(ws_url, max_size=None, open_timeout=10) as ws:
        cdp = _CDP(ws)
        target_id = None
        try:
            res = await cdp.send("Target.createTarget", {"url": page_url})
            target_id = res["targetId"]
            att = await cdp.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
            session_id = att["sessionId"]
            await cdp.send("Runtime.enable", session_id=session_id)

            expr = _FETCH_JS % json.dumps(video_url)
            out = await cdp.send(
                "Runtime.evaluate",
                {"expression": expr, "awaitPromise": True, "returnByValue": True},
                session_id=session_id,
                timeout=timeout,
            )
            value = out.get("result", {}).get("value")
            if not value:
                raise RuntimeError(f"bridge returned no value: {out}")
            data = json.loads(value)
            if not data.get("ok"):
                raise RuntimeError(f"in-page fetch failed: {data}")
            return base64.b64decode(data["b64"])
        finally:
            if target_id:
                try:
                    await cdp.send("Target.closeTarget", {"targetId": target_id})
                except Exception:  # noqa: BLE001
                    pass
            cdp.close()
