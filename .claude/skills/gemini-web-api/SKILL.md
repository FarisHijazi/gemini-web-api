---
name: gemini-web-api
description: "Use Google Gemini (the gemini.google.com web app) programmatically via a local OpenAI-compatible API — chat, true streaming, tool calling, vision, image generation, and Veo video. Runs with uvx straight from git, no install step. Covers Google multi-login profiles (u/N) and switching them when image/video daily quotas run out. Use when asked to run a prompt through Gemini, generate an image or video with Gemini/Veo, get a second opinion from Gemini, or point an OpenAI-compatible client at Gemini."
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

## 1. Start the server (once)

It must be running before any command. `GEMINI_AUTHUSER=N` picks the Google
multi-login profile (the `N` in `gemini.google.com/u/N/app`; omit for `u/0`).

```bash
GEMINI_AUTHUSER=1 nohup $GW gemini-web-api >/tmp/gemini-web-api.log 2>&1 &
# wait until ready:
until curl -sf -m2 http://localhost:8100/health >/dev/null; do sleep 2; done
```

Check / stop:
```bash
curl -s http://localhost:8100/health     # {"status":"ok"}
fuser -k 8100/tcp                        # stop (do NOT pkill -f main.py)
tail -20 /tmp/gemini-web-api.log
```

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
plain server GET 403s. Opt in by giving the server a Chrome debug port:

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.config/google-chrome"
GEMINI_CDP_URL=http://localhost:9222 GEMINI_AUTHUSER=1 nohup $GW gemini-web-api \
  >/tmp/gemini-web-api.log 2>&1 &
```

Each job downloads in its own tab keyed by `job_id`, so parallel jobs never collide.

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
signed in, start on each `N` and send a cheap chat request — a working profile
answers, an unused index errors.

## Guidance for Claude

- Start the server once, then reuse it; don't restart per command.
- On media quota failure (or a 600s video timeout), **switch profiles** rather
  than retrying the same one.
- Chat returning a 500 / API error `1097` means a stale session — the server
  self-heals on retry; if it persists, restart it.
- Never claim an image or video file was downloaded without verifying real bytes
  on disk.
