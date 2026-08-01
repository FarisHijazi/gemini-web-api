# 2026-08-01 — Parallel tab pool, auto-reload, staleness fixes (oud session)

**Lineage:** user (business-nofomo "oud" session): "make the chrome extension possible to
work in parallel by putting some IDs on them … FIX AND USE GEMINI API", then a 3-hour
autonomous mandate ("fix this gemini web api extension and auth … extensively test it…
restarting all of chrome would work, I'm fine with that too"). Built on top of the
jumana session's uncommitted extension work (that session died to login expiry).

## What changed

### chrome_backend.py — Hub is now a POOL
- Every Gemini tab is a worker (`TabConn`: tab_id, authuser, busy, eligible).
- `acquire(authuser=None, exclude_key=None)`: newest-first pick, strict per-account
  matching (quota is per-account — never silently burns another account), waits on an
  `asyncio.Event` when all busy, immediate named errors when no (matching) tab.
- Disconnect fails ONLY that tab's in-flight jobs (`job_conn` map).
- Tab-shaped failures (composer/send/no-reply/disconnect) retry ONCE on another tab.
- `eligible`: tabs that connect from inside a HUMAN conversation are never dispatched
  to; worker identity persists across reloads via sessionStorage `gcb_worker` → hello
  `worker:true` (fixes reconnect-inside-bridge-conversation poisoning).
- Timeouts raise NAMED errors (bare `asyncio.TimeoutError` str() is "" — cost an hour
  of blind debugging as `502 …failed: `).
- `/v1/status` now lists `tabs` (id, authuser, busy, eligible, href).
- `/v1/images/generations` accepts `authuser` to route to that account's tab.

### extension 0.3.0
- content.js: per-tab `TAB_ID` (sessionStorage) + `authuser` (from /u/N/) in hello.
- Replies always go over the CURRENT socket (`sendMsg`) — a reconnect mid-job used to
  drop the reply on the closed socket and the server timed out blind.
- Chat completion is text-STABILITY based — the 2026-07 UI keeps a visible "Stop
  response" button after completion, which stalled every chat to the 290s hard cap.
- Auto-dismisses the first-use "Keep in mind / Got it" onboarding dialog (blocked ALL
  generation on a fresh account and looked exactly like a quota stall).
- Quota-limit reply detection → fast fail instead of a 420s timeout.
- **Stale-refresh**: idle worker tabs reload every ~5-6 min. Empirical: a fresh tab
  finishes an image in 14-90s; after ~10 idle min the SAME tab times out every job.
  Protections: never reloads a human tab (worker flag), a focused tab, or a composer
  with typed text.
- Generation guard: newest injected script instance wins (marker in the shared DOM —
  isolated worlds don't share `window`); superseded/orphaned instances close their WS.
- background.js: auto-injects content.js into all Gemini tabs on install/reload/startup
  (`scripting` + `tabs` perms) and honors a reload relay; PLUS a `chrome.alarms` poll of
  `GET /v1/extension/pending-reload` every 60s as the backup reload path.
- `POST /v1/extension/reload` → broadcast + pending flag → the extension reloads and
  re-injects itself. content.js edits alone don't even need that: unpacked-extension
  content scripts are read from disk per injection, and stale-refresh reloads pages.

### Chrome launch flags (persisted)
`~/.local/share/applications/google-chrome.desktop` override adds
`--disable-background-timer-throttling --disable-renderer-backgrounding
--disable-backgrounding-occluded-windows` so hidden worker tabs keep full timers.

## Verified
- 6/6 pytest (incl. new tests/pool_test.py: acquire/release, strict authuser, waiting,
  no double-booking).
- 2 images in PARALLEL on u/2+u/3: 88s wall for both, real PNG bytes via /files/.
- Single watched job: 35s and 14s runs. Strict authuser error: instant, named.
- Full reload cycle server→extension→reinject verified via DOM generation marker.
- Saudi-dialect chat VO line generated through the pool.

## Same-account concurrency: CONFIRMED and FIXED (per-account media lock)

Gemini web runs at most **one image generation per Google account** — the concurrent
loser silently starves (no error, no spinner) until our timeout. Evidence:
- 4-parallel run, 2 jobs on u/2: winner 14s, loser 420s-timeout.
- Same losing prompt re-run SOLO on u/2: **102s, success**.
- Fix: `ChromeManager.generate_media` now wraps per-account `asyncio.Lock`
  (`_generate_media_locked`; internal retry calls the locked body directly to avoid
  deadlock). Live-verified: 2 jobs fired concurrently at u/2 → hub log shows the second
  dispatched exactly when the first finished; 14s + 16s, both real PNGs. 6/6 pytest.

## Disconnect resilience: verified live

Chat job dispatched to a u/3 tab; that Chrome tab was closed mid-job. Hub logged
`failed on tab-o1m2l3 (gemini tab tab-o1m2l3 disconnected) -- retrying on another tab`,
the retry landed elsewhere and the request still returned in 18s. Other tabs unaffected.

## Media account AUTO-FAILOVER (added later same day, user request)

`ChromeManager.generate_media` now walks accounts: requested `authuser` first, then
every other account with a connected tab. Quota-shaped failures put the account in a
cooldown (`GEMINI_MEDIA_QUOTA_COOLDOWN`, 30 min) so later requests skip it; attempts
after the first use `GEMINI_MEDIA_ATTEMPT_TIMEOUT` (180s) instead of the full budget.
`GEMINI_MEDIA_EXCLUDE_AUTHUSERS` (default empty) hard-bans accounts — but indices reshuffle on re-login, so re-verify after any sign-in/out. Returns
`(media, text, served_authuser)`; the images response and `/v1/status`
(`media_quota_cooldown`) expose the state. 4 new unit tests (10/10 total).
**Live-proven:** with u/5+u/0 stalled and u/1/u/2 busy, one request walked the pool
and returned a real PNG from u/3.

Root-cause find while testing: the first-use **"Keep in mind / Got it" notice can pop
AFTER the prompt is sent**, covering the response — dismissing it only at job start
misses it (burned u/5's first job; the image actually rendered behind the dialog).
`dismissOnboarding()` is now retried on every tick of both wait loops.

## Open / gotchas
- u/5 runs the **Pro** model by default — image gen there can exceed the 180s
  failover cap; pin `authuser=5` (full timeout) or switch that tab's model to Flash.
- **Chat has no `authuser` routing** — only `/v1/images/generations` accepts it;
  `/v1/chat/completions` ignores an `authuser` body field and uses any tab. Fine for now
  (chat quota is generous; routing exists for media quota), but don't assume chat pins.
- **u/3 image quota exhausted 2026-08-01** (~4 attempts incl. starved ones burned it):
  solo image on a FRESH u/3 tab fails with render-timeout, exactly like the starvation
  signature — quota exhaustion and concurrency starvation are indistinguishable from the
  outside. u/2 kept generating fine. u/0 also limited ("Image Generation Limit Reached").
- Onboarding auto-dismissal ran on u/2 (first Gemini use of that account).

## Late-evening addendum (sign-out incident + exclusion dropped)

Restarting `gemini-cdp-chrome` is suspected of signing desktop Chrome **out of
Google entirely** (second incident of the 1PSIDTS-rotation class; service left
stopped). After the user re-logged in, the multi-login indices RESHUFFLED — the
work account moved u/4 → u/7, silently invalidating the index-based media
exclusion. Indices were re-mapped via `myaccount.google.com/u/N/` and, per user
decision (2026-08-02), `GEMINI_MEDIA_EXCLUDE_AUTHUSERS` now defaults to EMPTY —
no account is excluded; the knob remains for anyone who needs it, with the
caveat that any index-based ban must be re-verified after every sign-in/out.
