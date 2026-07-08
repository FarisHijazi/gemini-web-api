# True token streaming (replacing the faked SSE)

**Date:** 2026-07-08

## The problem

`/v1/chat/completions` with `stream:true` was **fake streaming**: the server
called `generate_content` (one-shot), buffered the *entire* reply, then sliced
the finished string into SSE `chat.completion.chunk` deltas with a local
word-chunker. It satisfied the OpenAI SSE contract but gave zero latency benefit
— the first delta only appeared after the whole reply existed. A benchmark made
it obvious: **TTFT ≈ total** (e.g. 8.00s / 8.00s on `gemini-3-flash`).

## The fix

`gemini_webapi` already exposes real streaming: `GeminiClient.generate_content_stream`
is an async generator that yields `ModelOutput` chunks as they arrive off the
`StreamGenerate` response, and each `ModelOutput.text_delta` holds **only the new
characters** since the previous yield (the library diffs the progressive
snapshots for us). We just weren't using it.

Changes:
- `gemini_pool.py` — added `manager.generate_stream(...)`, an async generator
  wrapping `generate_content_stream` with the same one-shot re-init retry as
  `generate`, guarded so it only retries if **nothing was yielded yet** (can't
  cleanly restart mid-stream).
- `server.py` — the streaming branch now `async for out in manager.generate_stream(...)`
  and emits `out.text_delta` as a content delta the moment it arrives. Generated
  media (images/videos) isn't part of `text_delta`, so it's appended as a final
  delta after the loop (`_media_markdown`, split out of `render_output`).
- **Tool calling still buffers** — parsing the `{"tool_calls":[…]}` JSON needs the
  complete reply, so `req.tools` requests keep the buffered path (streaming a
  partial JSON block to an agentic client is worse than useless).

Non-streaming (`generate`) is unchanged.

## Verification

`tools/stream_probe.py` timestamps each SSE delta: **31 chunks arriving over a
~5.2s spread** (first token ~11.8s, last ~17.0s) — deltas genuinely trickle in
over the generation window instead of dumping at once.

`tools/benchmark.py` (`gemini-3-flash`, ~668-token generations, 3 runs):

| metric | before (fake) | after (true) |
|---|---|---|
| TTFT (median) | 8.00s (= total) | **13.99s < total**, min **1.61s** |
| total (median) | 8.00s | 17.60s |
| gen rate (first→last token) | n/a (all at once) | **~140 tok/s** |

The upstream Gemini latency is highly variable (min/max TTFT ranged 1.6–29.8s
across runs), so treat the medians as ballpark — the point is TTFT is now
consistently *below* total (real streaming) rather than equal to it. Non-stream
and tool-calling round-trips re-verified working after the change.
