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


def cmd_doctor(args) -> None:
    """Diagnose auth/cookie problems — the #1 cause of 'it doesn't work'."""
    print("gemini-web-api doctor\n")
    ok = True

    # 1. Where do credentials come from?
    env_psid = os.getenv("GEMINI_1PSID")
    if env_psid:
        print(f"credentials: GEMINI_1PSID env var (len {len(env_psid)})")
        print(f"  GEMINI_1PSIDTS: {'set' if os.getenv('GEMINI_1PSIDTS') else 'NOT set (usually fine)'}")
    else:
        print("credentials: local Chrome cookie store (GEMINI_1PSID not set)")
        try:
            from . import config
        except ImportError:
            print("  ! cannot import config"); return
        print(f"  chrome dir : {config.CHROME_DIR} "
              f"({'exists' if os.path.isdir(config.CHROME_DIR) else 'MISSING'})")
        stores = config._cookie_stores()
        print(f"  cookie DBs : {len(stores)} found")
        for s in stores[:5]:
            print(f"      {s}")
        try:
            psid, psidts = config.get_cookies()
        except Exception as e:  # noqa: BLE001
            psid = psidts = None
            print(f"  ! cookie read failed: {type(e).__name__}: {e}")
        print(f"  __Secure-1PSID  : {'found' if psid else 'NOT FOUND'}")
        print(f"  __Secure-1PSIDTS: {'found' if psidts else 'not found (usually fine)'}")
        try:
            print(f"  full cookie jar : {len(config.get_full_jar())} cookies")
        except Exception as e:  # noqa: BLE001
            print(f"  ! full jar failed: {type(e).__name__}: {e}")
        if not psid:
            ok = False
            print("""
  FIX: no usable Chrome cookies on this machine. Best option first:
    (a) AUTOMATIC — point at a logged-in Chrome over DevTools; cookies
        (including httpOnly __Secure-1PSID) are harvested for you:
          google-chrome --remote-debugging-port=9222 \\
              --user-data-dir="$HOME/.config/google-chrome"
          export GEMINI_CDP_URL=http://localhost:9222
    (b) install/open Chrome here and log in to https://gemini.google.com
    (c) MANUAL fallback — supply cookies explicitly:
          export GEMINI_1PSID='<__Secure-1PSID value>'
          export GEMINI_1PSIDTS='<__Secure-1PSIDTS value>'
        (Chrome DevTools > Application > Cookies > https://gemini.google.com)
    Note: on Linux the cookie DB is encrypted — the login keyring must be
    unlocked, otherwise use (a) or (c).""")

    print(f"\n  this shell's GEMINI_AUTHUSER: {os.getenv('GEMINI_AUTHUSER') or '(unset)'}")
    print("  (the server has its own config — shown below — which is what matters)")

    # 2. Is a server reachable, and how is IT actually configured?
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=5) as r:
            print(f"\nserver at {BASE}: {json.loads(r.read().decode()).get('status')}")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"\nserver at {BASE}: NOT REACHABLE ({type(e).__name__})")
        print("  FIX: systemctl --user start gemini-web-api")
        print("       (or, if not installed:  nohup gemini-web-api &)")
    else:
        try:
            with urllib.request.urlopen(BASE + "/v1/status", timeout=5) as r:
                st = json.loads(r.read().decode())
            print(f"  active profile   : u/{st.get('authuser')}")
            fb = st.get("authuser_fallbacks") or []
            print(f"  quota failover   : {'u/' + ', u/'.join(fb) if fb else 'OFF '
                                          '(set GEMINI_AUTHUSER_FALLBACKS to auto-switch profiles)'}")
            bridge = st.get("cdp_bridge")
            print(f"  video downloads  : {'ON (CDP bridge)' if bridge else 'OFF — jobs return a browser-only URL'}")
            print(f"  video timeout    : {st.get('video_timeout_s')}s")
            if st.get("api_key_required"):
                print("  auth             : API key required")
        except Exception:  # noqa: BLE001
            print("  (server predates /v1/status — restart it to see its config)")

    print("\nresult:", "OK" if ok else "PROBLEMS FOUND (see FIX above)")
    if not ok:
        sys.exit(1)


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

    d = sub.add_parser("doctor", help="diagnose cookie/auth/server problems")
    d.set_defaults(func=cmd_doctor)

    args = p.parse_args()
    if args.base:
        global BASE
        BASE = args.base.rstrip("/")
    args.func(args)


if __name__ == "__main__":
    main()
