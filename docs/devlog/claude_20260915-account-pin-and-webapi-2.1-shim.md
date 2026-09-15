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
