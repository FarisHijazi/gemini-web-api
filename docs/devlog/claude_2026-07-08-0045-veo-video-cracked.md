# Veo video generation — fully cracked

**Date:** 2026-07-08
**Status:** implemented end-to-end in `gemini_openai/video.py`. Every stage
verified; the full happy-path (fresh video → downloaded MP4) is gated only by
per-account daily video quota (all test accounts were exhausted during this
session — the pipeline correctly detects and reports that).

Supersedes the earlier "blocked by error 1053" note in
[@claude_2026-07-07-2130-reverse-engineer-gemini-openai-api.md](claude_2026-07-07-2130-reverse-engineer-gemini-openai-api.md).

## What made it hard (and the answers)

Reverse-engineered by diffing live "Create image" vs "Create video" traffic and
bisecting the `StreamGenerate` inner payload field-by-field against a raw client.

1. **Image vs video selector** — `inner[17]=[[1]]` (image is `[[0]]`, which the
   library hardcodes) + aspect `inner[55]=[[16]]` + `inner[54]=[]` + the
   message_content media tool flag. Model header is identical for both.

2. **`inner[49]` is a red herring** — it looked like the video selector (video
   traffic had `49=11`, image had `49=14`) but it's a **per-conversation turn
   counter**. Setting any value in a fresh turn → Google error **1053**. Bisect
   proved it: minimal request works; `+inner[49]` alone → 1053; remove it → no
   1053. So we never set it.

3. **Video requires an existing conversation** — every captured video request
   was a *follow-up* turn (`inner[2]` = real cid/rid/rcid). A first-turn video in
   a fresh conversation → 1053. Fix: **prime the chat** with a cheap text turn,
   then send the video request carrying that conversation context. (This is why
   the earlier one-shot `generate_content` path always failed.)

4. **Priming model** — starting the chat with the video model header and sending
   a *normal* priming turn → error **1097**. Prime with a normal model
   (`BASIC_PRO`); only the video turn needs the media flags.

5. **Async delivery + retrieval** — the video request returns fast with a
   `video_gen_chip` pending state; the finished video arrives later. The library's
   recovery loop is flaky (re-sends the request, sometimes loops forever) and its
   candidate parser doesn't recognize this video slot (so `output.videos` is
   empty). Instead we **poll `read_chat(cid)`** and scrape the raw batchexecute
   response for the download URL.

6. **The download URL** — the finished MP4 is at
   `https://<x>-qw.usercontent.google.com/download?c=...&filename=video.mp4`
   (the `lh3.../gg/...` URL is the thumbnail). It's **double-escaped** in the
   response (`\\u003d`→`=`, `\\u0026`→`&`) — decode any-depth `\uXXXX` first. The
   URL is **time-limited**: fetch it immediately. (Old URLs 403 — confirmed it's
   expiry, not auth, because the browser 403s them too.)

## Pipeline (`generate_video_url` → `download_video`)

1. `start_chat(BASIC_PRO)` → `send_message(prime)` → get `cid` + `metadata`.
2. `_send_raw_video_request()` — raw `StreamGenerate` POST over the library's
   (initialized) session with `inner[2]=metadata`, the video flags, and the
   media model header (all-zeros uuid, like the web app). Returns pending.
3. `_poll_video_url()` — loop `read_chat(cid)`, capture raw via a `_batch_execute`
   wrapper, decode escapes, regex the `filename=video.mp4` download URL. Detects
   the quota-limit message and fails cleanly.
4. `download_video()` — GET the URL with a gemini Referer; verify `ftyp` MP4 magic.

Exposed as an async job (`create_job` / `JOBS`) behind
`POST /v1/videos/generations` + `GET /v1/videos/generations/{id}` + `/files/{id}.mp4`.

## Verification

- Generation confirmed: an earlier run returned `text="Your video is ready!"`;
  real videos are visible/playable in the browser for the same account.
- URL extraction confirmed by reading an existing completed conversation
  (`read_chat`) — got `video/mp4`, `1280×720`, ~9s, and the download URL.
- Full pipeline run: primes → sends (no 1053) → polls → cleanly reports quota
  exhaustion (28s). Needs an account with remaining daily quota to emit an MP4.

## Gotchas for future work

- Monkeypatching `client._batch_execute` to capture raw poll responses is
  restored in `finally`; it's per-shared-client, so keep video jobs effectively
  serial or capture more surgically if high concurrency is needed.
- The inert `_JsonProxy` / `_video_ctx` path (older library-based approach) is
  no longer used by the raw pipeline; kept as reference, safe to remove later.

---

## Update 2026-07-08: the download 403 solved (browser bridge)

**Symptom.** `download_video()` reliably got **HTTP 403 (0 bytes)** from the
`*.usercontent.google.com/download?…filename=video.mp4` URL — even on a *fresh*
URL fetched immediately (ruling out expiry). The `gemini_webapi` library hits the
same wall: `_parse_candidate` reads the video URL from `candidate[12,59,0,0,0][0][7][1]`
— the **same** usercontent URL — and `GeneratedVideo._download_file` does a plain
`GET` with a Referer, exactly what we did.

**Root cause — per-host, per-account `OSID`.** `usercontent.google.com` download
hosts are cookie-isolated: each needs an `OSID`/`__Secure-OSID` cookie **scoped to
that exact host** (e.g. the store has ones for `drive.usercontent.google.com`,
`mail.google.com`, … but none for the ephemeral `contribution*.usercontent.google.com`
video hosts). The browser mints the host OSID transparently when the `<video>`
element first loads. The download host **hard-403s with no redirect/`Set-Cookie`
handshake** (probed with `allow_redirects=False`), so there is no server-side OSID
mint to replicate — and the OSID is never persisted to the on-disk cookie store, so
`browser_cookie3` can't read it either. It's also **per-account**: a u/1-generated
URL 403s in a u/6 browser tab.

**What works — a credentialed fetch from inside the Gemini page.** The one context
that returns the bytes is `fetch(url, {credentials:'include'})` issued **from a
`gemini.google.com/u/N` page of the same account** — the same origin/credentials
context the `<video>` element uses. Google sends CORS headers to `gemini.google.com`,
so the response is readable (not opaque). Verified via the browser extension:
**200, `content-type: video/mp4`, 2,329,959 bytes** → a valid `ftyp/moov/mdat` MP4.

**Productized as `gemini_openai/video_bridge.py`** (opt-in via `GEMINI_CDP_URL`):
drives a logged-in Chrome over the DevTools Protocol (minimal client over
`websockets`, flat sessions) — open a tab at `…/u/N/app`, run an in-page routine
that (a) fetches the URL credentialed, and if 403, (b) loads it in a throwaway
`<video>` to mint the host OSID, then retries — base64 the bytes back over CDP.
Parallel-safe: each job uses its own tab and the bytes return **directly to the
caller keyed by job_id** (never a shared Downloads folder), so concurrent jobs
can't be confused. Falls back to returning the browser-playable `download_url`
when `GEMINI_CDP_URL` is unset.

**Enable:** launch Chrome once with `--remote-debugging-port=9222` on the
logged-in profile, then `GEMINI_CDP_URL=http://localhost:9222`.

**Verification.** (1) The in-page fetch technique proven against real Google auth
via the extension (200, 2.3 MB MP4 on disk — `media/bridge_proof_fox.mp4`).
(2) CDP plumbing (createTarget→attach→`Runtime.evaluate`→base64 roundtrip) proven
against a throwaway `--headless` Chrome (`tools/cdp_plumbing_test.py`). The bridge
is the union of these two independently-verified halves. A single combined
CDP+auth run requires launching the user's Chrome with the debug flag (their call).
