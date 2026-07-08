"""Benchmark the gemini-scraper API: time-to-first-token (TTFT) and tokens/sec.

The server now does TRUE streaming: it forwards each upstream `text_delta` as it
arrives (via gemini_webapi's generate_content_stream), so the measured TTFT is a
real time-to-first-token and deltas trickle in over the generation window. The
"post_ttft tok/s" figure is now a real generation rate (tokens emitted between
first and last token), and "effective tok/s" is output_tokens / total wall time.

Token counts are approximate (no exact Gemini tokenizer available): we use a
~4-chars-per-token heuristic. Treat tok/s as a relative/ballpark figure.

Usage:
    PYTHONPATH=. uv run --active python tools/benchmark.py
    GEMINI_API_BASE=http://localhost:8100 BENCH_MODEL=gemini-3-flash \
        BENCH_RUNS=3 uv run --active python tools/benchmark.py
"""

from __future__ import annotations

import json
import os
import statistics
import time

import httpx

BASE = os.getenv("GEMINI_API_BASE", "http://localhost:8100").rstrip("/")
KEY = os.getenv("GEMINI_API_KEY", "")
MODELS = os.getenv("BENCH_MODEL", "gemini-3-flash,gemini-3-pro").split(",")
RUNS = int(os.getenv("BENCH_RUNS", "3"))

# A prompt that forces a reasonably long, deterministic-length generation so
# tok/s is measured over real output volume rather than a one-word reply.
PROMPT = (
    "Write a detailed, self-contained explanation of how HTTPS/TLS establishes "
    "a secure connection, covering the handshake, certificates, key exchange, "
    "and symmetric encryption. Aim for about 350-450 words. Plain prose."
)


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if KEY:
        h["Authorization"] = f"Bearer {KEY}"
    return h


def _approx_tokens(text: str) -> int:
    # No exact Gemini tokenizer available; ~4 chars/token is the usual ballpark.
    return max(1, round(len(text) / 4))


def bench_stream(model: str) -> dict:
    body = {"model": model, "messages": [{"role": "user", "content": PROMPT}], "stream": True}
    t0 = time.perf_counter()
    ttft = None
    text_parts: list[str] = []
    with httpx.stream("POST", f"{BASE}/v1/chat/completions", json=body,
                      headers=_headers(), timeout=300) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            try:
                delta = json.loads(payload)["choices"][0]["delta"]
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
            piece = delta.get("content")
            if piece:
                if ttft is None:
                    ttft = time.perf_counter() - t0
                text_parts.append(piece)
    total = time.perf_counter() - t0
    text = "".join(text_parts)
    tokens = _approx_tokens(text)
    gen_window = max(1e-6, total - (ttft or 0.0))
    return {
        "ttft": ttft or total,
        "total": total,
        "tokens": tokens,
        "eff_tok_s": tokens / total,          # honest: output over full wall time
        "post_ttft_tok_s": tokens / gen_window,  # cadence after first chunk (mostly artifact)
        "chars": len(text),
    }


def bench_nonstream(model: str) -> dict:
    body = {"model": model, "messages": [{"role": "user", "content": PROMPT}]}
    t0 = time.perf_counter()
    r = httpx.post(f"{BASE}/v1/chat/completions", json=body, headers=_headers(), timeout=300)
    total = time.perf_counter() - t0
    r.raise_for_status()
    text = r.json()["choices"][0]["message"].get("content") or ""
    tokens = _approx_tokens(text)
    return {"total": total, "tokens": tokens, "eff_tok_s": tokens / total, "chars": len(text)}


def _agg(vals: list[float]) -> str:
    if len(vals) == 1:
        return f"{vals[0]:.2f}"
    return f"{statistics.median(vals):.2f} (min {min(vals):.2f}, max {max(vals):.2f})"


def main() -> None:
    print(f"target: {BASE}   runs/model: {RUNS}")
    print(f"prompt: ~400-word generation ({len(PROMPT)} char prompt)\n")
    for model in MODELS:
        model = model.strip()
        print(f"=== {model} ===")
        # warm up (first call may re-init the client) — not counted
        try:
            bench_nonstream(model)
        except Exception as e:  # noqa: BLE001
            print(f"  SKIP ({type(e).__name__}: {str(e)[:80]})\n")
            continue

        s_runs = [bench_stream(model) for _ in range(RUNS)]
        n_runs = [bench_nonstream(model) for _ in range(RUNS)]

        toks = [r["tokens"] for r in s_runs]
        print(f"  output: ~{round(statistics.median(toks))} tokens "
              f"(~{round(statistics.median([r['chars'] for r in s_runs]))} chars)")
        print("  STREAMING (true token streaming):")
        print(f"    TTFT (s):            {_agg([r['ttft'] for r in s_runs])}   "
              f"<- real time to first token")
        print(f"    total (s):           {_agg([r['total'] for r in s_runs])}")
        print(f"    gen rate tok/s:      {_agg([r['post_ttft_tok_s'] for r in s_runs])}   "
              f"<- tokens between first & last token")
        print(f"    effective tok/s:     {_agg([r['eff_tok_s'] for r in s_runs])}   "
              f"<- output_tokens / total wall time")
        print("  NON-STREAMING:")
        print(f"    total (s):           {_agg([r['total'] for r in n_runs])}")
        print(f"    effective tok/s:     {_agg([r['eff_tok_s'] for r in n_runs])}")
        print()

    print("note: tok/s is approximate (no exact Gemini tokenizer; ~4 chars/token).")
    print("note: streaming forwards upstream text_delta live; TTFT < total means real streaming.")


if __name__ == "__main__":
    main()
