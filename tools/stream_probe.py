"""Prove true streaming: print the arrival time + size of each SSE delta."""

import json
import time

import httpx

BASE = "http://localhost:8100"
body = {
    "model": "gemini-3-flash",
    "messages": [{"role": "user", "content":
                  "Count slowly from 1 to 30, one number per line, with a short note on each."}],
    "stream": True,
}

t0 = time.perf_counter()
n = 0
first = None
last = 0.0
total_chars = 0
with httpx.stream("POST", f"{BASE}/v1/chat/completions", json=body, timeout=120) as r:
    for line in r.iter_lines():
        if not line.startswith("data: "):
            continue
        p = line[6:]
        if p == "[DONE]":
            break
        try:
            d = json.loads(p)["choices"][0]["delta"]
        except Exception:  # noqa: BLE001
            continue
        c = d.get("content")
        if not c:
            continue
        now = time.perf_counter() - t0
        n += 1
        total_chars += len(c)
        if first is None:
            first = now
        last = now
        if n <= 12 or n % 10 == 0:
            print(f"  chunk {n:3d} @ {now:6.2f}s  (+{len(c)} chars): {c[:42]!r}")

print(f"\nchunks: {n}   first-token: {first:.2f}s   last-token: {last:.2f}s   chars: {total_chars}")
print(f"spread (last-first): {last - (first or 0):.2f}s  -> if >0, deltas truly arrived over time")
