# Setting up the `dev` host: extension behind Local Network Access, per-account downloads, 9:16

**Lineage.** User, 2026-10-13 (business-nofomo session): *"you totally have the gemini
web api and you can generate free videos using gemini, get an agent to fully setup gemini
web api"*. Done by a Claude Code subagent on host `dev`. Goal: images **and** Veo video over
`POST localhost:8100/...`, so agents stop hand-driving the Gemini UI. Branch
`fix/extension-ws-via-service-worker`, not pushed.

## Starting state

- The systemd service ran `uvx --from git+…/gemini-web-api` (GitHub `8f31e0a`), while the
  local checkout was 6 commits behind with byte-identical staged work (= `1ac14ba`).
  Fast-forwarded; the staged copy is kept in `git stash` as a backup.
- `/v1/status`: `tabs: []`. The bridge extension had never been loaded in this Chrome
  (one profile, `Profile 3`). Account not pinned (trap 1).

## What blocked, and the fixes

1. **Loading the extension.** `chrome://` is off-limits to the browser automation, and
   Load unpacked opens a GTK folder picker. Driven with `xdotool` + `scrot` on `:0`
   (Ctrl+L, type the path). Developer mode was already on.
2. **Local Network Access.** Tabs had the content script (`data-gcb-gen` set) but never
   connected: Chrome 155 put up *"gemini.google.com wants to access other apps and
   services on this device"*. It went away unanswered, and
   `navigator.permissions.query({name:"loopback-network"})` then read `denied`.
   The extension worker's own `fetch(localhost:8100/…)` (the reload poll) still got
   through, so **the worker owns the WebSocket now** (extension 0.4.0): `content.js`
   `openSocket()` is a WebSocket-shaped proxy over a `gcb-ws` Port. All three accounts
   connected after one `POST /v1/extension/reload`, with no prompt and no user click.
3. **Downloads from any account but u/0 got 403** (video, then image after failover). Both
   hosts serve the multi-login owner selected by `authuser`. Measured in a u/1 tab:
   usercontent video: none → 403, `&authuser=0` → 403, `&authuser=1` → 200 (1.6 MB mp4).
   lh3 image: none → error, `?authuser=1` → 1376×768. Fix: `video.with_authuser()` in
   both fetch paths.
4. **Image failover skipped working accounts.** The 3 h quota cache only records video
   exhaustion, but images honoured it too, so one failed video run made images refuse
   u/1 and u/2. Images now pass `use_quota_cache=False`.
5. **9:16 was a guess.** `inner[55]=[[9]]` → landscape output. The UI's own Portrait
   request (DevTools → Copy as fetch, only `f.req` parsed) sends `[[17]]`, landscape
   `[[16]]`. Switched to named constants. **But the API still got 1280×720 with
   `[[17]]`**, while the UI's portrait clip is a real 720×1280. The UI request also
   differs: tool flag `message_content[9]=[…,[[null,null,null,2]]]` (ours `1`),
   `inner[17]=[[0]]` (ours `[[1]]`), plus a 99-wide payload with e.g.
   `inner[30]=[4,16]`. All video quota was gone before those could be tested.

Dead ends, kept so nobody repeats them: page-world XHR/fetch/JSON.stringify hooks
capture nothing (Gemini's requests skip them), and `chrome.webRequest` with `requestBody`
in the extension saw no gemini.google.com requests on this policy-managed Chrome.
DevTools' Network panel driven by xdotool worked.

## Quota behaviour observed

- An exhausted video account now replies with an ordinary finalized text turn
  (*"I can create more videos as soon as your limit resets"*), not a stop, so the
  poller waits out `GEMINI_VIDEO_TIMEOUT` per spent account. The service now uses 240 s
  (real runs took 61–64 s). Not phrase-matched, on purpose (trap 7).
- u/0 (Plus) and u/1 each made 2 videos today before the UI greyed out "Create video".
  u/0 limit: "available again Oct 13 at 6:00 PM" (≈15:00 UTC). **u/2 has no video tool.**
- Images: 15 s (u/0) and 25 s (failover to u/1 incl. one refusal). u/0 images ran out
  mid-session because other agents share that account.

## Verified

- 49 pytest pass (new `tests/media_account_test.py`, updated `media_fetch_test.py`).
- Image over HTTP: u/0 1376×768 JPEG; failover u/0→u/1 1376×768 JPEG, both real bytes.
- Video over HTTP: u/0 `vid_0384…` completed in 64 s, `/files/…mp4` 2,330,962 B,
  h264+aac 1280×720 10.0 s (requested 9:16, before the aspect fix).
- `systemctl --user restart gemini-web-api` ×5: tabs reconnect within 25 s every time.

## Host config (not in the repo)

- `~/.config/gemini-web-api/env`: `GEMINI_CHROME_ACCOUNT=buzamahmooza@gmail.com`,
  `GEMINI_AUTHUSER=0`, `GEMINI_AUTHUSER_FALLBACKS=0,1,2`, `GEMINI_VIDEO_TIMEOUT=240`,
  `GEMINI_BACKEND=webapi`. In `auto`, chat would be typed into any connected bare `/app`
  tab, and other agents' hand-driven Gemini tabs register as eligible workers too.
- Drop-in `~/.config/systemd/user/gemini-web-api.service.d/local-checkout.conf` runs this
  checkout (`uv run --frozen --project …`) until the branch is merged and pushed.
- Worker tabs `u/0`, `u/1`, `u/2` (`/app`) open in Chrome; closing them loses that account.

## Open

- Portrait video: test tool flag `2`, then `inner[17]=[[0]]`, once quota resets.
- Merge + push the branch, then delete the drop-in to return to `uvx --from git+…`.

---

## Follow-up 16:35–17:10 UTC: every image request was a 500, and portrait video is solved

**Lineage.** Asked by another Claude session (business-nofomo) on the user's behalf: image
generation worked at 13:00 UTC, then every `POST /v1/images/generations` returned 500.

### Root cause: Google moved u/2 to an "Auto"-only model picker

```
ValueError: Unknown model name: 'gemini-3-flash'. Available registered models: gemini-auto
```

Not a dependency bump (uvx still had `gemini-webapi` 2.1.1, same as `uv.lock`) and not our
env. gemini-webapi builds its model registry **per account** from `GET_USER_STATUS` at init.
Probed all three accounts with the same code:

| account | registered models |
|---|---|
| u/0, u/1 | `gemini-flash-lite` (3.5), `gemini-flash` (3.6), `gemini-pro` (3.1) |
| u/2 | **`gemini-auto` only** |

The image route walks `GEMINI_AUTHUSER_FALLBACKS`; once u/0 and u/1 ran out it switched the
shared client to u/2 and **stayed there**, so every later request asked u/2 for
`gemini-3-flash` and died before reaching Google. The ValueError also escaped the failover loop.

**Fix (PR https://github.com/FarisHijazi/gemini-web-api/pull/8, squash-merged):**
`gemini_pool._for_account()` resolves the name against the live client and sends
`model=None` (Google's default for that account) when it is absent; applied to both
`generate` and `generate_stream`. 4 tests in `tests/gemini_pool_test.py` (3 failed first).
Trap 14 in CLAUDE.md. Qodo's review errored twice ("Sorry, something went wrong"), so it
left no comments to address.

### Portrait video: tool flag 2 was the missing piece

One change on top of `inner[55]=[[17]]`: `message_content[9]` tool flag `2` for portrait
(landscape keeps `1`). **First run: 720×1280, 10.0 s, real portrait framing** (job
`vid_64a9fc35…`, u/0, ~70 s). Hypothesis 2 (`inner[17]=[[0]]`) was not needed and not
spent. Tests in `tests/video_payload_test.py`; trap 13 rewritten.

### Host notes

- The u/0 and u/2 `/app` worker tabs were open but silent (`/v1/status` showed only u/1 and
  a u/2 *conversation* tab). `POST /v1/extension/reload` did not wake them; re-navigating
  each tab to its own `/app` URL did. Probably Chrome discarded them in the background.
- `uvx --from git+…` re-resolved the merged main without `--refresh` once the cache had been
  warmed with `uvx --refresh-package gemini-web-api --from git+… python -c …`.
- The temporary drop-in `gemini-web-api.service.d/local-checkout.conf` ran the checkout for
  the portrait test only, and was removed after that PR merged.
