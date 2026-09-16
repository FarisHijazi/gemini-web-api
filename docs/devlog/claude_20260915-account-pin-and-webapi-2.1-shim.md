# 2026-09-15 — the server was speaking as the wrong Google account, and the 2.1 upgrade

Two things in one session: a **privacy bug** found while answering "which account
is this using?", and the **gemini-webapi 2.1 compatibility work** that was already
in flight.

## 1. The account bug (the important one)

**Symptom:** none. That is what made it bad — the server worked perfectly while
speaking as an account nobody chose.

`config._cookie_stores()` globbed every Chrome profile and sorted the cookie
stores by mtime, newest first. `get_full_jar()` then took the first store holding
a `__Secure-1PSID`. With several Google accounts signed in to Chrome, that means:

> the account the server speaks as is whichever Chrome profile you last opened.

Measured on this machine, five profiles were signed in and **all five** had the
cookie. Running the resolution three times in a row returned two different
accounts:

```
winner: Profile 13   <work account A>
winner: Profile 13   <work account A>
winner: Profile 14   <work account B>
```

Never the intended personal account, and the winner changed between runs as
browser activity rewrote mtimes. Any history, quota use, or abuse signal landed
on a work account.

### Fix

- `GEMINI_CHROME_ACCOUNT=<email>` pins the account. Email, not profile
  directory: `Profile 13` is opaque and machine-specific, an email is not.
- An unknown pinned account **raises**, listing the accounts that are signed in.
  A silent fallback is what caused the bug; falling back "helpfully" would
  reintroduce it.
- Unpinned ordering got a total-order tiebreak (`(-mtime, path)`) so it is at
  least deterministic within a machine, and the mtime behaviour is documented as
  the trap it is rather than left to be rediscovered.
- The chosen account is printed once, on first cookie use:
  `[gemini] using Google account: … [pinned|AUTO-SELECTED …]`. A wrong account is
  now visible in the first line of the log instead of invisible forever.
- `~/bin/gemini-web-api-server` pins the personal account by default.

Six tests in `tests/config_test.py` cover the pin, case/space tolerance, the
precedence of `GEMINI_CHROME_PROFILE`, the loud failure, and determinism.

## 2. gemini-webapi 2.0.0 → 2.1.x

Researched whether anything after 2.0.0 was worth taking. Verdict: **yes, but not
for the reason hoped.**

| Hoped for | Reality |
|---|---|
| Finer streaming | **Not delivered.** 2.1 replaced the O(n²) `parse_response_by_frame` with a stateful `StreamingFrameParser`, which is a CPU win, but chunk granularity is set by Google's `batchexecute` framing and is identical in both versions. The wait-then-burst behaviour is unchanged. |
| `No CID found to recover` retry loop | **Fixed.** 2.0.0 raised immediately; 2.1 calls `close()` first so `@running(retry=5)` re-inits and mints a fresh session id instead of re-sending the same prompt six times at a backend that just refused it. This is the failure this server actually hits under concurrency. |
| Cookie-refresh reliability | **Fixed in 2.1.1.** Network failures at init are no longer swallowed by a bare `except Exception` and mis-counted as "these cookies are invalid", so a transient blip can no longer overwrite a good `__Secure-1PSIDTS` cache with a bad one. |
| Token usage | **No.** `client.usage_info` reports credits/quota windows, not prompt/completion tokens. An honest OpenAI `usage` block is still impossible. |

### What actually breaks, and the shim

1. **`Model.*_THINKING` is gone.** `*_LITE` is a *new cheap tier* with its own
   model id (`cf41b0e0dd7d53e5`), **not** `*_THINKING` renamed
   (`5bf011840784117a`). Verified directly against an installed 2.1.1.
2. **Every `model_name` lost its version**: `gemini-3-pro` → `gemini-pro`.

The fix is to stop using the enum at all. `config` now maps public names to
**name strings**, which work on both versions — proven, not assumed:

- 2.0.x registers `gemini-3-pro`, so it is an exact match.
- 2.1.x resolves through `MODEL_PREFIX_RE = ^gemini-(?:\d+(?:\.\d+)?-)?`, which
  strips the version, so `gemini-3-pro` still matches `gemini-pro`.

So one table serves both and the public API stays stable regardless of what
upstream renames internally. The thinking tier degrades to flash where it no
longer exists, and `list_public_models()` stops advertising it there — never
advertise a model that is silently served as a different one.

3. **`inner_req_list` grew 69 → 81.** The video code both patched a payload
   (`len(obj) == 69`) and hand-built one (`[None] * 69`). The patch check failed
   **silently** — video requests would have degraded to plain chat with no error.
   `video.py` now learns the width from whatever the library serializes
   (`_inner_len`) and builds to match.

Also worth recording: `_video_ctx` is set **only** by `tools/` diagnostics, never
in the production path, so production video relies on the hand-built payload —
which is why the width has to be learned unconditionally rather than inside the
video-mode branch.

`chrome_backend._model_name()` needed no change and in fact improved: with a
string it returns the stable public name instead of the library's internal one.

`account.py`'s `Endpoint` monkeypatch survives untouched — verified by importing
every production module under both versions.

### Verification

`tests/` 25/25 under **both** 2.0.0 and 2.1.1, plus an out-of-tree shim check
asserting resolution, advertisement, and payload-width learning on each. Pin
widened to `>=2.0.0,<2.2` — not unbounded, because `Model` is documented as
pending removal and 2.2 will delete it.

**Not yet exercised:** a real Veo video round-trip on 2.1.x. The width fix is
reasoned and unit-tested but video is the one path that fails silently, so it
should be confirmed live before anyone relies on video under 2.1.

## 3. Launcher bug found on the way

`gemini-web-api-server stop` killed the pid in its pidfile and reported success
while the actual server kept running and holding :8100 — `uv run` execs python as
a *child*, so the recorded pid was a wrapper. `stop` now walks descendants by
PPID (`pgrep -P` — ancestry, not command-line matching) and verifies the port
went quiet before claiming it stopped.

## 4. Why it was logging the account out (the actual complaint)

The account pin was the answer to "which account", but the *reason* it mattered
was that using an account logs it out of Chrome repeatedly.

**Mechanism, read out of the library rather than guessed:** `rotate_1psidts()`
POSTs to Google's `ROTATE_COOKIES` endpoint, which mints a **new**
`__Secure-1PSIDTS` and invalidates the old one. We called it every 540s
(`auto_refresh=True, refresh_interval=540`). Chrome's stored copy therefore went
stale every nine minutes, and Google signed the browser session out.

**Evidence.** The rotation cache is keyed on `__Secure-1PSID`, so each entry
names the browser session it rotated. Three entries existed; mapping each back to
a live Chrome profile:

```
f.hijazi@…            session still matches Chrome   rotated  14.7 min ago
(no live Chrome profile)  STALE                      rotated  66.3 min ago
(no live Chrome profile)  STALE                      rotated  17.1 min ago
```

Two sessions this server had rotated no longer existed in any Chrome profile —
i.e. those browser sessions had been replaced. One of them was 17 minutes old,
inside the window of that session's own testing.

**Fix — find the owner.** `rotate_1psidts` has exactly one call site in
`client.py`, inside `start_auto_refresh`, gated on `auto_refresh`; the token
fetch never rotates. So `auto_refresh=False` removes the rotation entirely. The
rule is now that whoever supplies the cookies owns refreshing them
(`config.rotate_cookies_ourselves()`):

- **from Chrome** → Chrome owns the session and keeps it fresh; we read and never
  write. The pool already re-inits on `AuthError`, and `get_cookies()` re-reads
  Chrome on every call, so a stale token self-heals.
- **from `GEMINI_1PSID`** → nothing else keeps them alive, so we must refresh.

This is the "don't add a second write path to a shared resource" rule applied to
a browser session.

## 5. A mistake worth recording: the cache filename *is* the credential

While listing the cache entries to remove the work-account ones, the removal
script printed **full paths** — and the library names each file
`.cached_cookies_<__Secure-1PSID>.json`. Every other script in this session
deliberately printed only lengths and short hashes of cookie *values*; the
filename was the hole in that discipline, and it leaked one live Google session
cookie plus two already-dead ones into the session transcript.

Blast radius was local: `~/.claude` is a public repo, but `projects/` is
gitignored and no transcript file is tracked.

The correct response to a leaked credential is to invalidate it, not to chase
copies. Recorded as trap 5 in `CLAUDE.md`: print `basename[:28]` or a hash, never
a cache path.

## 6. Video was broken by 2.1, and the fix was to delete code

Confirming media end-to-end turned up the failure the width fix was *not*
enough to prevent. A Veo job ran the full 600s and failed with:

> timed out after 600s on profile(s) 0 — this almost always means the daily
> video quota is exhausted there.

That diagnosis was wrong: the account had **24,078 of 24,192 credits**. The job
generated nothing because the request we sent was no longer the request the web
app sends.

`video.py` hand-built `inner_req_list` from a live capture. gemini-webapi 2.1
widened it 69 → 81 **and added fields**, which `client.py` now sets:

```python
inner_req_list[68] = 1          # we hand-set 2
inner_req_list[79] = 1          # we left None (or a model_number off the header)
inner_req_list[80] = 2 if extended_thinking else 1   # we left None
```

Learning the *width* kept the list the right length but left the new slots
empty, so the payload was structurally valid and semantically wrong — exactly
the silent degrade this path is prone to.

### Fix: let the library build it

The library already does everything the hand-rolled request did, and does it
against the current format:

- builds `message_content` (7 fields; our proxy pads to 10 and sets `[9]`)
- mints `uuid_val`, sets `inner_req_list[59]`, **and** sends the matching
  `x-goog-ext-525005358-jspb` header
- sends the model header from the model — `VIDEO_MODEL` is already a dict in
  that shape, and 2.1 additionally appends the session id to it
- uploads attachments via `send_message(files=...)`

So `generate_video_url` now primes the conversation, sets `_video_ctx`, and
calls `chat.send_message(prompt, files=...)`. `_JsonProxy` overlays only the
genuinely video-specific fields (`message_content[9]`, `inner[17]`, `[54]`,
`[55]`), all below index 69 and therefore width-agnostic.

Deleted: `_build_video_inner`, `_send_raw_video_request`,
`_upload_reference_frames`, `VIDEO_MODEL_HEADER`, the `urllib.parse` import, and
the width-learning global that existed only to feed the hand-built payload.

**Net: 17 lines added, 88 removed.** The remaining coupling to Google's wire
format is four indices instead of twenty, and the version-sensitive part now
belongs to the library we pin.

### Images

Verified working on 2.1.1: two generations, 14.0s and 14.6s, each returning one
`lh3.googleusercontent.com` URL. Those URLs 403 from a script **by design** —
Google serves generated images only to an authenticated browser context, and
unlike video the bridge cannot rescue them (CORS). That is unchanged by 2.1.

### Test hygiene

`MEDIA_DIR` defaults to the relative `"media"`, so every chrome-backend test run
wrote fixtures into the repo's own media directory — 16 of them were sitting
there (70-byte PNGs, 25-byte `FAKE_MP4_BYTES_…` files), in a directory the
server also serves over `/files`. The test now points the module at a temp dir.
