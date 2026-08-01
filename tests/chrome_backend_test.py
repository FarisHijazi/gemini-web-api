#!/usr/bin/env python3
"""Chrome-backend integration test.

Boots the REAL gemini_openai.server with GEMINI_BACKEND=chrome, connects a fake
extension to /ws, and drives the OpenAI endpoints through the whole package:
health/status report the backend, chat completions (stream + non-stream) relay to
the extension, errors propagate, and image/video return 501.

Run:  GEMINI_BACKEND=chrome uv run tests/chrome_backend_test.py
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time

os.environ["GEMINI_BACKEND"] = "chrome"  # must be set before importing the server

import httpx  # noqa: E402
import uvicorn  # noqa: E402
import websockets  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gemini_openai import server as srv  # noqa: E402


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = _free_port()
BASE = f"http://127.0.0.1:{PORT}"


def _run_server(sv: uvicorn.Server):
    asyncio.set_event_loop(asyncio.new_event_loop())
    sv.run()


async def fake_extension(stop: asyncio.Event, mode: str = "echo"):
    async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws") as ws:
        await ws.send(json.dumps({"type": "hello"}))
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.3)
            except asyncio.TimeoutError:
                continue
            msg = json.loads(raw)
            mtype = msg.get("type")
            rid = msg.get("id")
            if mtype in ("image", "video"):
                # 1x1 PNG / tiny fake video bytes, base64
                if mtype == "image":
                    b64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
                           "C0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
                    media = [{"kind": "image", "mime": "image/png", "b64": b64}]
                else:
                    import base64 as _b64
                    b64 = _b64.b64encode(b"FAKE_MP4_BYTES_0123456789").decode()
                    media = [{"kind": "video", "mime": "video/mp4", "b64": b64}]
                await ws.send(json.dumps({"type": "result", "id": rid, "media": media, "text": ""}))
                continue
            if mtype != "chat":
                continue
            prompt = msg["prompt"]
            if mode == "error":
                await ws.send(json.dumps({"type": "error", "id": rid, "message": "boom"}))
            elif mode == "stream":
                for piece in ["Hel", "Hello wor", "Hello world!"]:
                    await ws.send(json.dumps({"type": "delta", "id": rid, "text": piece}))
                    await asyncio.sleep(0.05)
                await ws.send(json.dumps({"type": "result", "id": rid, "text": "Hello world!"}))
            else:
                await ws.send(json.dumps({"type": "result", "id": rid, "text": f"echo: {prompt}"}))


def _check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        raise SystemExit(f"FAILED: {name}")


async def scenario():
    async with httpx.AsyncClient(timeout=30) as c:
        # backend reported everywhere (forced chrome mode for this process)
        h = (await c.get(f"{BASE}/health")).json()
        _check("health backend_mode == chrome", h["backend_mode"] == "chrome")
        _check("health active_backend == chrome", h["active_backend"] == "chrome")
        _check("health extension_connected false", h["extension_connected"] is False)
        st = (await c.get(f"{BASE}/v1/status")).json()
        _check("status backend_mode == chrome", st["backend_mode"] == "chrome")

        # forced-chrome with no extension -> error names the missing tab
        r = await c.post(f"{BASE}/v1/chat/completions",
                         json={"messages": [{"role": "user", "content": "hi"}]})
        _check("chat with no extension errors", r.status_code >= 400)
        _check("error mentions missing tab", "gemini tab" in r.text.lower())

        # connect the fake extension
        stop = asyncio.Event()
        ext = asyncio.create_task(fake_extension(stop, "echo"))
        await asyncio.sleep(0.5)
        h = (await c.get(f"{BASE}/health")).json()
        _check("health extension_connected true", h["extension_connected"] is True)

        # non-streaming chat
        r = await c.post(f"{BASE}/v1/chat/completions",
                         json={"model": "gemini-3-pro",
                               "messages": [{"role": "user", "content": "ping"}]})
        j = r.json()
        _check("chat 200", r.status_code == 200)
        _check("chat content echoed",
               j["choices"][0]["message"]["content"] == "echo: ping")
        _check("openai object shape", j["object"] == "chat.completion")

        stop.set()
        await ext

        # streaming chat
        stop2 = asyncio.Event()
        ext2 = asyncio.create_task(fake_extension(stop2, "stream"))
        await asyncio.sleep(0.5)
        chunks = []
        async with c.stream("POST", f"{BASE}/v1/chat/completions",
                            json={"stream": True,
                                  "messages": [{"role": "user", "content": "go"}]}) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        break
                    chunks.append(json.loads(payload))
        text = "".join(ch["choices"][0]["delta"].get("content", "") for ch in chunks)
        _check("stream reassembles full text", text == "Hello world!")
        stop2.set()
        await ext2

        # error propagation
        stop3 = asyncio.Event()
        ext3 = asyncio.create_task(fake_extension(stop3, "error"))
        await asyncio.sleep(0.5)
        r = await c.post(f"{BASE}/v1/chat/completions",
                         json={"messages": [{"role": "user", "content": "x"}]})
        _check("extension error surfaces", r.status_code >= 400 and "boom" in r.text)
        stop3.set()
        await ext3

        # models still listed
        r = await c.get(f"{BASE}/v1/models")
        _check("models list", any(m["id"] == "gemini-3-pro" for m in r.json()["data"]))

        # --- media via the extension (image + video job lifecycle) ---
        stopm = asyncio.Event()
        extm = asyncio.create_task(fake_extension(stopm, "echo"))
        await asyncio.sleep(0.5)

        # image: synchronous, returns a /files URL to a real PNG on disk
        r = await c.post(f"{BASE}/v1/images/generations", json={"prompt": "a red apple"})
        j = r.json()
        _check("image 200 + chrome backend", r.status_code == 200 and j.get("backend") == "chrome")
        url = j["data"][0]["url"]
        _check("image url is /files/*.png", "/files/" in url and url.endswith(".png"))
        fr = await c.get(url)
        _check("image bytes are a real PNG", fr.status_code == 200 and fr.content[:8] == b"\x89PNG\r\n\x1a\n")

        # video: async job -> poll -> completed -> /files URL to real bytes
        r = await c.post(f"{BASE}/v1/videos/generations", json={"prompt": "apple rolling"})
        _check("video job 202 queued", r.status_code == 202 and r.json()["status"] in ("queued", "processing"))
        jid = r.json()["id"]
        vurl = None
        for _ in range(50):
            jr = (await c.get(f"{BASE}/v1/videos/generations/{jid}")).json()
            if jr["status"] == "completed":
                vurl = jr["url"]
                break
            if jr["status"] == "failed":
                raise SystemExit(f"video job failed: {jr}")
            await asyncio.sleep(0.2)
        _check("video job completed with url", bool(vurl))
        vr = await c.get(vurl)
        _check("video bytes saved", vr.status_code == 200 and vr.content == b"FAKE_MP4_BYTES_0123456789")

        stopm.set()
        await extm


def routing_checks():
    """Auto-mode routing is a pure function of (BACKEND, extension online, files).

    Verifies the 'one server, both backends' contract without needing cookies:
    auto prefers the extension when connected, falls back to webapi when not, and
    always sends vision/file input to webapi (the extension can't do it).
    """
    print("auto-routing (pure-function) checks:")
    from gemini_openai.chrome_backend import TabConn
    prev_mode = srv.config.BACKEND
    prev_conns = dict(srv._chrome_hub.conns)
    try:
        srv.config.BACKEND = "auto"

        srv._chrome_hub.conns.clear()  # no tab connected
        _check("auto + no tab -> active webapi", srv.active_backend_name() == "webapi")
        _check("auto + no tab -> chat via webapi", srv.pick_manager() is srv.webapi_manager)

        srv._chrome_hub.conns[999] = TabConn(ws=object(), key=999, tab_id="t")  # pretend a tab
        _check("auto + tab -> active chrome", srv.active_backend_name() == "chrome")
        _check("auto + tab -> chat via chrome", srv.pick_manager() is srv.chrome_manager)
        _check("auto + tab + files -> webapi (vision)",
               srv.pick_manager(has_files=True) is srv.webapi_manager)

        srv.config.BACKEND = "webapi"
        _check("forced webapi ignores tab", srv.pick_manager() is srv.webapi_manager)
    finally:
        srv.config.BACKEND = prev_mode
        srv._chrome_hub.conns.clear()
        srv._chrome_hub.conns.update(prev_conns)


def main():
    config = uvicorn.Config(srv.app, host="127.0.0.1", port=PORT, log_level="warning")
    sv = uvicorn.Server(config)
    t = threading.Thread(target=_run_server, args=(sv,), daemon=True)
    t.start()
    for _ in range(50):
        try:
            httpx.get(f"{BASE}/health", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    print("chrome-backend integration tests:")
    asyncio.run(scenario())
    routing_checks()
    print("ALL PASS")
    sv.should_exit = True


def test_chrome_backend():
    """pytest entry point."""
    main()


if __name__ == "__main__":
    main()
