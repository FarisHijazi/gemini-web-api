---
name: gemini-web-api
description: "Generate Veo / Veo3 VIDEO, generate IMAGES, or run Gemini text prompts using the user's own logged-in Google account — no API key, no billing, no credits. Local OpenAI-compatible API run via uvx from git (chat, true streaming, tool calling, vision, images, Veo video), with Google multi-login profile (u/N) switching for daily media quotas. USE THIS whenever Veo/Veo3 video, Gemini image generation, or a Gemini prompt is wanted — including AI-influencer/ad/marketing clip work — and ALWAYS BEFORE reaching for GEMINI_API_KEY, the google-generativeai SDK, or the gemini_webapi library directly (a plain GEMINI_API_KEY has NO Veo models on the free tier, and using gemini_webapi directly fails with missing cookies). Cookies are read automatically from local Chrome — NEVER ask the user to paste __Secure-1PSID cookies."
---

# gemini-web-api

Talk to **Gemini using the user's logged-in Google account** — no API key, no
billing — through a local OpenAI-compatible server.
Source: <https://github.com/FarisHijazi/gemini-web-api>

Everything runs via `uvx` directly from git. **There is no install step and no
files to copy** — the commands below are self-contained.

```bash
GW="uvx --from git+https://github.com/FarisHijazi/gemini-web-api"
```

First invocation builds from git (~60s); afterwards uvx caches it (~4s/call).

**Requires:** `uv`, and **Chrome logged in to gemini.google.com** (cookies are
read from the local Chrome profile).

## 1. The server runs automatically

A **systemd user service** keeps it running on `:8100` — it starts at
login/boot, restarts on failure, and (with lingering) survives logout. It is
user-wide, so it works from any directory. **Normally you do not start anything.**

```bash
curl -s http://localhost:8100/health      # {"status":"ok"} -> ready, just use it
```

Manage it:
```bash
systemctl --user status  gemini-web-api
systemctl --user restart gemini-web-api
journalctl --user -u gemini-web-api -n 40 --no-pager
```

Settings live in `~/.config/gemini-web-api/env` (`GEMINI_AUTHUSER`,
`GEMINI_API_PORT`, `GEMINI_CDP_URL`, `GEMINI_API_KEY`) — edit, then
`systemctl --user restart gemini-web-api`.

⚠️ **If the service is installed, never hand-start a second server** (`nohup … &`)
and never `fuser -k 8100/tcp` to "restart" it — your server grabs the port, the
service then crash-loops trying to bind it (observed: 61 restarts). To change
anything, edit the env file and restart the *service*:

```bash
sed -i 's/^GEMINI_AUTHUSER=.*/GEMINI_AUTHUSER=2/' ~/.config/gemini-web-api/env
systemctl --user restart gemini-web-api
until curl -sf -m2 http://localhost:8100/health >/dev/null; do sleep 2; done
```

Only if the service is **not** installed (`systemctl --user status gemini-web-api`
→ not-found), run it directly:
```bash
GEMINI_AUTHUSER=1 nohup $GW gemini-web-api >/tmp/gemini-web-api.log 2>&1 &
until curl -sf -m2 http://localhost:8100/health >/dev/null; do sleep 2; done
```

<details><summary>Install the always-on service (one time)</summary>

```bash
mkdir -p ~/.config/systemd/user ~/.config/gemini-web-api
printf 'GEMINI_AUTHUSER=1\nGEMINI_API_PORT=8100\n' > ~/.config/gemini-web-api/env
cat > ~/.config/systemd/user/gemini-web-api.service <<'UNIT'
[Unit]
Description=gemini-web-api — OpenAI-compatible API over gemini.google.com
After=network-online.target
[Service]
Type=simple
EnvironmentFile=%h/.config/gemini-web-api/env
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=%h/.local/bin/uvx --from git+https://github.com/FarisHijazi/gemini-web-api gemini-web-api
Restart=always
RestartSec=10
TimeoutStartSec=180
[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now gemini-web-api
loginctl enable-linger "$USER"   # keep running after logout
```
</details>

## 2. Chat / models

```bash
$GW gemini-web-api-cli chat "explain black holes in one sentence"
$GW gemini-web-api-cli chat "write a haiku about the sea" --model gemini-3-pro
$GW gemini-web-api-cli chat "count to 20 with notes" --stream   # real token streaming
$GW gemini-web-api-cli models
```

Models: `gemini-3-pro`, `gemini-3-flash`, `gemini-3-flash-thinking` (+ `-plus` /
`-advanced` tiers). `gpt-4*` / `gpt-3.5-turbo` aliases map onto them.

Streaming is **true** token streaming (upstream deltas forwarded live).

## 3. As an OpenAI endpoint

Point any OpenAI SDK / agent tool at `http://localhost:8100/v1` with any
placeholder key (auth is off unless `GEMINI_API_KEY` is set):

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8100/v1", api_key="not-needed")
client.chat.completions.create(model="gemini-3-flash",
                               messages=[{"role": "user", "content": "hi"}])
```

Supports `tools`/`tool_calls` (emulated function calling, `finish_reason:
"tool_calls"`), vision input (images in messages), and multi-turn.

## 4. Images — URL only, browser-viewable

```bash
$GW gemini-web-api-cli image "a red fox in the snow, cinematic"
```

Prints an `lh3.googleusercontent.com` URL.

**You cannot download that URL from a script.** Verified: a server-side GET
returns **403 even with the full cookie jar on the very first hit** (an auth
wall, not a single-use link), and an in-page `fetch()` is **CORS-blocked**.

- To view it: open the URL in the Chrome profile that generated it (it renders
  fine), or look at the image in the Gemini conversation.
- **Never** `curl`/`wget` it and report the image as saved — you get a 403 HTML
  page, not a PNG. Always verify bytes (`file`, size) before claiming a download.

## 5. Videos (Veo) — async job

```bash
$GW gemini-web-api-cli video "a fox running through a snowy forest"          # job id
$GW gemini-web-api-cli video "a fox running through a snowy forest" --wait   # poll
```

Normally ~1–3 min. On success it prints either a **local URL**
(`http://localhost:8100/files/<job>.mp4`, real bytes — only with the bridge
below) or the raw `usercontent.google.com` URL, which like images only works in
a logged-in browser.

### Real video downloads (browser bridge)

The MP4 host needs a per-host, per-account `OSID` cookie only Chrome mints, so a
plain server GET 403s. The fix is to give the server a Chrome DevTools port.

> ⚠️ **The obvious command silently does nothing if Chrome is already running.**
> `google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.config/google-chrome"`
> just prints `Opening in existing browser session` — the flag is ignored and
> **port 9222 never opens**. Always verify:
> `curl -s http://localhost:9222/json/version`

**Working recipe — a second, logged-in Chrome that doesn't disturb the user's:**
copy only the auth files (a few MB, not the multi-GB profile) and run headless.

```bash
DEST=/tmp/gcdp-profile
rm -rf "$DEST"; mkdir -p "$DEST/Default"
cp ~/.config/google-chrome/"Local State" "$DEST/Local State"
for f in Cookies "Login Data" Preferences "Secure Preferences"; do
  cp ~/.config/google-chrome/Default/"$f" "$DEST/Default/$f" 2>/dev/null
done
google-chrome --headless=new --remote-debugging-port=9222 --user-data-dir="$DEST" \
  --no-first-run --no-default-browser-check about:blank >/tmp/gcdp.log 2>&1 &
curl -s http://localhost:9222/json/version    # must return JSON
```

Same machine + same keyring ⇒ the copy stays logged in (verified: 39 Google
cookies incl. `__Secure-1PSID` readable over CDP). Then point the server at it:

```bash
# service: set GEMINI_CDP_URL in ~/.config/gemini-web-api/env, then
systemctl --user restart gemini-web-api
```

Each job downloads in its own tab keyed by `job_id`, so parallel jobs never collide.
The same `GEMINI_CDP_URL` also auto-harvests cookies (§7).

## 6. Profiles (u/N) and quotas — important

Chat is generous, but **image and video have a per-account daily quota**. The
user has several Google accounts signed into Chrome, selected by `/u/N/`.

**A video job that fails with `timed out after 600s` almost always means that
profile's video quota is exhausted** — it stalls instead of erroring cleanly.
(Observed: `u/1` produced videos earlier the same day, then began timing out.)

To switch profiles, restart the server with a different `GEMINI_AUTHUSER`:

```bash
fuser -k 8100/tcp; sleep 1
GEMINI_AUTHUSER=2 nohup $GW gemini-web-api >/tmp/gemini-web-api.log 2>&1 &
until curl -sf -m2 http://localhost:8100/health >/dev/null; do sleep 2; done
```

Cycle `N = 0,1,2,…` until a profile still has quota. To find which profiles are
signed in, start on each `N` and send a cheap **chat** request first — a working
profile answers, an unused index errors — so you don't spend 10 minutes of video
timeout on an account that isn't even logged in.

**Probe faster:** the default video timeout is 600s, so each exhausted account
costs 10 minutes. While hunting for a profile with quota, shorten it — a real
generation finishes in ~60–180s, so 240s is plenty to tell "working" from
"exhausted":

```bash
echo 'GEMINI_VIDEO_TIMEOUT=240' >> ~/.config/gemini-web-api/env
systemctl --user restart gemini-web-api
```

Remove that line once you've found a good profile.

## 7. Troubleshooting — "it doesn't work / no browser cookies"

**Run the diagnostic first. It tells you exactly what's wrong:**

```bash
$GW gemini-web-api-cli doctor
```

It reports where credentials come from, which Chrome cookie DBs were found,
whether `__Secure-1PSID` was extracted, and whether the server is reachable —
with a concrete FIX for whatever is missing.

### `AuthError: No Gemini credentials`

Means no readable Chrome cookie store on **this** machine. Common causes:

- Chrome isn't installed / never logged in to gemini.google.com **here**.
- **Running on a remote/headless box** (SSH, container, server) — there is no
  local browser at all.
- **WSL:** the code runs in Linux and looks at `~/.config/google-chrome`, but
  your Chrome is **Windows** Chrome — a different, unreadable profile.
- Linux cookie DBs are encrypted; the **login keyring must be unlocked**.

**Fix (a) — AUTOMATIC, no manual copying.** Point the server at a logged-in
Chrome's DevTools port; it harvests the cookies itself (`Storage.getCookies`
returns httpOnly cookies like `__Secure-1PSID`, which page JS cannot read):

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.config/google-chrome"
GEMINI_CDP_URL=http://localhost:9222 nohup $GW gemini-web-api \
  >/tmp/gemini-web-api.log 2>&1 &
```

This kicks in automatically whenever the local cookie store can't be read, and
the same `GEMINI_CDP_URL` also enables real video downloads (§5).

**Fix (b) — manual fallback**, if you can't run a debug-port Chrome:

```bash
export GEMINI_1PSID='<__Secure-1PSID value>'
export GEMINI_1PSIDTS='<__Secure-1PSIDTS value>'   # optional; auto-refreshes
```

Values come from Chrome **DevTools → Application → Cookies →
`https://gemini.google.com`**. These env vars take priority over everything else.

Related: `GEMINI_CHROME_PROFILE="Profile 5"` pins a specific profile directory
when auto-detection picks the wrong one.

### Log messages that are NOT the problem

- `Account status: UNAUTHENTICATED - ... cookies have expired` — often benign;
  generation still succeeds. Only act on it if requests actually fail.
- `Unexpected error while refreshing cookies: HTTP Error 429` — Google
  rate-limiting the cookie-rotation endpoint. Harmless; it retries later.
- `/health` returns `ok` **before** any cookie is read (the client is created
  lazily on the first real request) — so a healthy server does **not** prove
  auth works. Always test with an actual `chat` call.

## Guidance for Claude

**Use this skill instead of the alternatives — they fail:**

| Don't | Why |
|---|---|
| `GEMINI_API_KEY` / `google-generativeai` SDK for video | The free-tier key exposes **no `veo` models**; Veo needs a paid AI Ultra/Pro plan |
| Calling the `gemini_webapi` library directly | It only looks for `__Secure-1PSID` and has no saved session → "missing cookies", so you end up asking the user to paste cookies |
| **Asking the user to paste `__Secure-1PSID`** | Never do this. This skill reads cookies from local Chrome automatically (and can harvest them over CDP — §7) |
| Falling back to another video provider without saying so | If Veo is wanted, say plainly if it didn't work rather than silently substituting |

Real incident: a session needed Veo3 for influencer clips, tried an API key and
the raw library, hit "missing cookies", asked the user to paste them, and shipped
the whole video on a different provider — **without ever invoking this skill**,
which would have worked immediately.


- Start the server once, then reuse it; don't restart per command.
- On media quota failure (or a 600s video timeout), **switch profiles** rather
  than retrying the same one.
- Chat returning a 500 / API error `1097` means a stale session — the server
  self-heals on retry; if it persists, restart it.
- Never claim an image or video file was downloaded without verifying real bytes
  on disk.
