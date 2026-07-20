# gemini-scraper — OpenAI-compatible API for gemini.google.com

An unofficial, self-hosted **OpenAI-compatible API** backed by the
[gemini.google.com](https://gemini.google.com/app) web app. Point any OpenAI
SDK at `http://localhost:8100/v1` and talk to Gemini using your logged-in Google
account — no official API key, no billing.

Built by reverse-engineering the web app's private `batchexecute` /
`StreamGenerate` RPC protocol. The heavy lifting of the protocol is done by the
maintained [`gemini_webapi`](https://github.com/HanaokaYuzu/Gemini-API) library;
this project adds the OpenAI-compatible HTTP layer, robust multi-account auth,
and media generation.

## Features

| Capability | Status |
|---|---|
| `POST /v1/chat/completions` — non-streaming | ✅ |
| `POST /v1/chat/completions` — **streaming (SSE)** | ✅ |
| **Tool / function calling** (`tools`, `tool_calls`, `tool_choice`) | ✅ emulated — works with agentic coding tools |
| `GET /v1/models` | ✅ (Gemini 3 pro / flash / thinking + tiers) |
| Vision input (images in messages, OpenAI format) | ✅ |
| Multi-turn conversations, system prompts | ✅ |
| `gpt-4` / `gpt-4o` / `gpt-3.5-turbo` aliases | ✅ (map to Gemini) |
| `POST /v1/images/generations` | ✅ returns a URL — [view in a logged-in browser](#images-are-browser-only) |
| `POST /v1/videos/generations` (Veo) | ✅ async job + poll — see [Video](#video-veo-status) |
| Google multi-login (`/u/N/`) accounts | ✅ |
| Automatic cookie / token refresh | ✅ |

## Requirements

- Python 3.12, [`uv`](https://docs.astral.sh/uv/)
- Google Chrome on the same machine, **logged in to gemini.google.com**
  (cookies are read straight from the local Chrome profile), or explicit cookies
  via env vars for headless/remote deploys.

## Quick start

**One line, no clone** (needs [`uv`](https://docs.astral.sh/uv/) + Chrome logged
in to gemini.google.com):

```bash
uvx --from git+https://github.com/FarisHijazi/gemini-web-api gemini-web-api
```

That fetches, installs into an isolated env, and starts the server on `:8100`.
Point at a specific account with `GEMINI_AUTHUSER=N` in front of it.

**Or from a clone** (for development):

```bash
uv sync                            # install deps
uv run --active python main.py     # start the server on :8100
```

Open the interactive docs / test console: **http://localhost:8100/docs**

### Use it with the OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8100/v1", api_key="not-needed")

# non-streaming
r = client.chat.completions.create(
    model="gemini-3-flash",
    messages=[{"role": "user", "content": "Explain black holes in one sentence."}],
)
print(r.choices[0].message.content)

# streaming
for ev in client.chat.completions.create(
    model="gemini-3-pro",
    messages=[{"role": "user", "content": "Write a haiku about the sea."}],
    stream=True,
):
    print(ev.choices[0].delta.content or "", end="", flush=True)
```

### curl

```bash
curl localhost:8100/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "gemini-3-flash",
  "messages": [{"role": "user", "content": "Reply with exactly: OK"}]
}'
```

## Tool / function calling (for coding agents like opencode, Cline, Cursor)

The Gemini web app has no native function-calling API, so it's **emulated via
prompt engineering** (the same approach as LiteLLM's `add_function_to_prompt`,
LocalAI, and `00bx/gemini-web-proxy`): incoming OpenAI `tools` schemas are
rendered into the prompt, Gemini is instructed to reply with a
`{"tool_calls":[…]}` JSON block, and that's parsed back into the OpenAI
`tool_calls` shape (`finish_reason: "tool_calls"`, arguments as a JSON string).
Multi-strategy parsing + `json_repair` handle malformed output; multi-line code
is kept out of the JSON via placeholder tokens (`USE_CODE_BLOCK_ABOVE`, etc.).
Streaming `tool_calls` deltas and the assistant/tool round-trip are supported.

Standard OpenAI `tools` requests just work:

```python
r = client.chat.completions.create(
    model="gemini-3-flash",
    messages=[{"role": "user", "content": "Weather in Tokyo? use the tool"}],
    tools=[{"type": "function", "function": {
        "name": "get_weather",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
    }}],
)
r.choices[0].message.tool_calls  # -> [get_weather({"city":"Tokyo"})]
```

### opencode

`~/.config/opencode/opencode.json` (set `tools: true` so opencode uses the
emulated function calling; use your own host instead of `localhost` if the server
runs elsewhere, e.g. over Tailscale):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "gemini-web": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Gemini Web (local)",
      "options": { "baseURL": "http://localhost:8100/v1", "apiKey": "not-needed" },
      "models": {
        "gemini-3-pro": { "name": "Gemini 3 Pro", "tools": true },
        "gemini-3-flash": { "name": "Gemini 3 Flash", "tools": true },
        "gemini-3-flash-thinking": { "name": "Gemini 3 Flash Thinking", "tools": true }
      }
    }
  }
}
```

Then: `opencode run -m gemini-web/gemini-3-flash "your prompt"` (verified working).

> Caveat: emulated tool calling is less token-efficient and less rock-solid than
> a native function-calling API — expect occasional parse retries on complex
> multi-tool turns. It works, but native APIs are more reliable.

## Configuration (environment variables)

| Var | Default | Purpose |
|---|---|---|
| `GEMINI_API_HOST` | `0.0.0.0` | bind host |
| `GEMINI_API_PORT` | `8100` | bind port |
| `GEMINI_API_KEY` | *(empty)* | if set, clients must send `Authorization: Bearer <key>` |
| `GEMINI_AUTHUSER` | *(none = u/0)* | Google multi-login account index — the `N` in `gemini.google.com/u/N/app` |
| `GEMINI_CHROME_PROFILE` | *(auto)* | pin a Chrome profile dir (e.g. `"Profile 5"`) instead of auto-picking the newest |
| `GEMINI_1PSID` / `GEMINI_1PSIDTS` | *(none)* | supply cookies explicitly (headless/remote); skips reading local Chrome |
| `GEMINI_PROXY` | *(none)* | HTTP proxy URL |
| `GEMINI_MEDIA_DIR` | `media` | where generated videos are saved |
| `GEMINI_VIDEO_TIMEOUT` | `600` | seconds before a video job fails |
| `GEMINI_CDP_URL` | *(none)* | Chrome DevTools endpoint (e.g. `http://localhost:9222`) enabling server-side video download via the browser bridge |

### Multi-account / choosing the right Google account

Google multi-login stores several accounts under one Chrome profile, selected by
a `/u/N/` path prefix. `__Secure-1PSID` alone only reaches the **default**
account (`u/0`). This project sends the **full `.google.com` cookie jar** plus
the `/u/N/` routing, so any signed-in account works — set `GEMINI_AUTHUSER=N`
(find `N` in the URL when that account is active in the browser).

```bash
GEMINI_AUTHUSER=5 uv run --active python main.py
```

## How it works

See [`docs/devlog/`](docs/devlog/) for the full reverse-engineering write-up.
In short:

- **Protocol** — `POST /_/BardChatUi/data/.../StreamGenerate`, body
  `f.req=[null,"<inner-json>"]` + `at=<token>`. The `SNlM0e` access token and
  `bl` build label are scraped from the app HTML.
- **Auth** — cookies read from the local Chrome store via `browser_cookie3`;
  `gemini_webapi` auto-refreshes the rotating `__Secure-1PSIDTS`.
- **OpenAI layer** — `gemini_openai/server.py` (FastAPI). Messages are flattened
  to a transcript; streaming is **true token streaming** — each upstream
  `text_delta` (from `gemini_webapi`'s `generate_content_stream`) is forwarded
  live as an SSE `chat.completion.chunk` delta as it arrives. (Tool-calling
  requests are the one exception: they buffer, since the full reply is needed to
  parse the `tool_calls` JSON.)

## Images are browser-only

`POST /v1/images/generations` returns an `lh3.googleusercontent.com` URL, but
**you cannot download that URL from a script**: Google serves generated images
only to an authenticated *browser* context. Verified — a server-side GET returns
**403 even with the full cookie jar on the very first hit** (so it's an auth wall,
not a single-use link), and an in-page `fetch()` is **CORS-blocked** (unlike the
video host, which is why the browser bridge rescues video but not images).

Open the URL in the Chrome profile that generated it (it redirects to an
`rd-gg-dl/…=s512` URL and renders fine), or just view the image in the Gemini
conversation. Don't `curl` it and assume you got a PNG — you'll get a 403 page.

## Video (Veo) status

Fully reverse-engineered and implemented (see
[`gemini_openai/video.py`](gemini_openai/video.py) and the
[devlog](docs/devlog/)). Video generation is **async**, so the API models it as
a job you poll:

```bash
# start a job
curl -X POST localhost:8100/v1/videos/generations \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"a red fox running through a snowy forest at sunrise","model":"gemini-3-pro"}'
# -> {"id":"vid_...","status":"queued"}

# poll until completed (takes ~1-3 min)
curl localhost:8100/v1/videos/generations/vid_...
# -> {"status":"completed","url":"http://<host>:8100/files/vid_....mp4","bytes":1234567}
```

**How it works** (the hard-won details):
- Video is selected by inner-payload fields `inner[17]=[[1]]` + aspect
  `inner[55]` + the media tool flag — the library hardcodes the *image* values.
- It **only works as a follow-up turn in an existing conversation**, so the
  pipeline primes a chat first (a fresh first-turn video request returns Google
  error `1053`). `inner[49]` is a turn counter — setting it manually also causes
  `1053`, so we don't.
- Generation is async: the request returns a pending state; we poll `read_chat`
  until the finished-video download URL (`usercontent.google.com/download?...
  filename=video.mp4`) appears.

**Downloading the MP4 — the usercontent OSID wall + the browser bridge.**
The finished video is served from `*.usercontent.google.com`, a **cookie-isolated,
per-account** host: it needs an `OSID` cookie scoped to that exact host that only
the browser mints (when the `<video>` element loads). A plain server-side GET —
and `gemini_webapi`'s own downloader — get a hard **403**. The bytes are reachable
only via a credentialed `fetch()` from inside a logged-in `gemini.google.com/u/N`
page (Google serves CORS there). `gemini_openai/video_bridge.py` automates this
over the Chrome DevTools Protocol; it's **opt-in**:

```bash
# 1. launch Chrome once with a debug port on your logged-in profile
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.config/google-chrome"
# 2. point the server at it
GEMINI_CDP_URL=http://localhost:9222 GEMINI_AUTHUSER=1 uv run --active python main.py
```

With `GEMINI_CDP_URL` set, completed jobs expose a local `url`
(`/files/<job_id>.mp4`) with the real bytes; each job downloads in its own browser
tab keyed by `job_id`, so **parallel jobs never collide**. Without it, the job
still completes and returns the browser-playable `download_url` (open it in an
authenticated browser). See the video devlog for the full reverse-engineering.

**Quota:** video has a **per-account daily limit**. When exhausted, a job fails
with `daily video generation quota exhausted`. Use `GEMINI_AUTHUSER` to point at
an account that still has quota. Chat/image generation are independent.

## Project layout

```
gemini_openai/
  config.py         # settings, cookie extraction, model name mapping
  gemini_pool.py    # shared, auto-refreshing GeminiClient
  account.py        # /u/N/ multi-account routing + full-jar auth
  openai_schemas.py # OpenAI request/response models + message flattening
  tools.py          # emulated function calling (prompt inject + parse-back)
  server.py         # FastAPI app (chat, models, images, videos, files)
  video.py          # Veo video trigger + async job store
  video_bridge.py   # CDP browser bridge: download videos past the usercontent OSID
main.py             # entrypoint (uvicorn)
cli.py              # tiny zero-dep CLI (chat / models / image / video)
tools/              # reverse-engineering & verification scripts
```

## Claude Code skill

A single self-contained `SKILL.md` at `~/.claude/skills/gemini-web-api/` — no
bundled scripts, nothing to install. Every command runs straight from git:

```bash
GW="uvx --from git+https://github.com/FarisHijazi/gemini-web-api"

GEMINI_AUTHUSER=1 nohup $GW gemini-web-api >/tmp/gemini-web-api.log 2>&1 &   # start
$GW gemini-web-api-cli chat "explain black holes in one sentence"
$GW gemini-web-api-cli chat "count to 20" --stream        # true token streaming
$GW gemini-web-api-cli image "a red fox in the snow"
$GW gemini-web-api-cli video "a fox in a snowy forest" --wait
```

It also documents the media gotchas: image/video have **per-account daily
quotas** (switch `GEMINI_AUTHUSER`), a video job failing with `timed out after
600s` means that profile's quota is spent, and image URLs are browser-only.

## CLI

A tiny stdlib-only CLI wraps the API (start the server first). Point it elsewhere
with `--base` or `GEMINI_API_BASE`. It also ships as a console script, so
`uvx --from git+https://github.com/FarisHijazi/gemini-web-api gemini-web-api-cli chat "hi"`
works with no clone:

```bash
python cli.py chat "explain black holes in one sentence"
python cli.py chat "write a haiku about the sea" --model gemini-3-pro --stream
python cli.py models
python cli.py image "a red fox in the snow, cinematic"
python cli.py video "a fox running through a snowy forest" --wait
```

> Unofficial project. Uses your personal Google session; respect Google's ToS
> and rate limits. Media generation (image/video) has per-account daily quotas.
