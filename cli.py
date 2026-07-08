#!/usr/bin/env python3
"""Tiny CLI for the gemini-scraper OpenAI-compatible API.

Zero third-party deps (stdlib only). Talks to a running server (default
http://localhost:8100 — override with --base or GEMINI_API_BASE). Start the
server first: `uv run --active python main.py`.

Examples
--------
    python cli.py chat "explain black holes in one sentence"
    python cli.py chat "write a haiku about the sea" --model gemini-3-pro --stream
    python cli.py models
    python cli.py image "a red fox in the snow, cinematic"
    python cli.py video "a red fox running through a snowy forest" --wait
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.getenv("GEMINI_API_BASE", "http://localhost:8100").rstrip("/")
KEY = os.getenv("GEMINI_API_KEY", "")


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if KEY:
        h["Authorization"] = f"Bearer {KEY}"
    return h


def _req(method: str, path: str, body: dict | None = None, stream: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=_headers(), method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=600)
    except urllib.error.HTTPError as e:
        sys.exit(f"error {e.code}: {e.read().decode('utf-8', 'replace')[:400]}")
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach {BASE} ({e.reason}). Is the server running?")
    if stream:
        return resp
    return json.loads(resp.read().decode())


def cmd_chat(args) -> None:
    body = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
        "stream": args.stream,
    }
    if not args.stream:
        r = _req("POST", "/v1/chat/completions", body)
        print(r["choices"][0]["message"].get("content") or "")
        return
    resp = _req("POST", "/v1/chat/completions", body, stream=True)
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            break
        try:
            delta = json.loads(payload)["choices"][0]["delta"]
        except (json.JSONDecodeError, KeyError, IndexError):
            continue
        sys.stdout.write(delta.get("content") or "")
        sys.stdout.flush()
    print()


def cmd_models(args) -> None:
    for m in _req("GET", "/v1/models")["data"]:
        print(m["id"])


def cmd_image(args) -> None:
    r = _req("POST", "/v1/images/generations", {"prompt": args.prompt, "model": args.model})
    for d in r.get("data", []):
        print(d.get("url", ""))


def cmd_video(args) -> None:
    job = _req("POST", "/v1/videos/generations", {"prompt": args.prompt, "model": args.model})
    jid = job["id"]
    print(f"job {jid}: {job['status']}", file=sys.stderr)
    if not args.wait:
        print(jid)
        return
    t0 = time.time()
    while True:
        time.sleep(args.interval)
        j = _req("GET", f"/v1/videos/generations/{jid}")
        st = j["status"]
        print(f"  [{int(time.time()-t0)}s] {st}", file=sys.stderr)
        if st == "completed":
            # local file (bridge) if present, else the browser-playable URL
            print(j.get("url") or j.get("download_url") or "")
            if j.get("download_error"):
                print(f"(server-side download skipped: {j['download_error']})", file=sys.stderr)
            return
        if st == "failed":
            sys.exit(f"video failed: {j.get('error')}")


def main() -> None:
    p = argparse.ArgumentParser(prog="gemini-cli", description="CLI for the gemini-scraper API")
    p.add_argument("--base", help="API base URL (default $GEMINI_API_BASE or http://localhost:8100)")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("chat", help="chat completion")
    c.add_argument("prompt")
    c.add_argument("--model", default="gemini-3-flash")
    c.add_argument("--stream", action="store_true")
    c.set_defaults(func=cmd_chat)

    m = sub.add_parser("models", help="list models")
    m.set_defaults(func=cmd_models)

    i = sub.add_parser("image", help="generate an image, print URL(s)")
    i.add_argument("prompt")
    i.add_argument("--model", default="gemini-3-pro")
    i.set_defaults(func=cmd_image)

    v = sub.add_parser("video", help="generate a video (async job)")
    v.add_argument("prompt")
    v.add_argument("--model", default="gemini-3-pro")
    v.add_argument("--wait", action="store_true", help="poll until completed")
    v.add_argument("--interval", type=float, default=8.0)
    v.set_defaults(func=cmd_video)

    args = p.parse_args()
    if args.base:
        global BASE
        BASE = args.base.rstrip("/")
    args.func(args)


if __name__ == "__main__":
    main()
