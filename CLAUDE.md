# CLAUDE.md — gemini-scraper

Unofficial **OpenAI-compatible API** over the gemini.google.com web app. Full
usage in [@README.md](README.md); reverse-engineering details in
[@docs/devlog/claude_2026-07-07-2130-reverse-engineer-gemini-openai-api.md](docs/devlog/claude_2026-07-07-2130-reverse-engineer-gemini-openai-api.md).

## Run

```bash
uv run --active python main.py            # server on :8100, docs at /docs
GEMINI_AUTHUSER=6 uv run --active python main.py   # target Google account u/6
```
Kill by port, not name: `fuser -k 8100/tcp` (`pkill -f main.py` kills the shell).

## Two backends, one server (`GEMINI_BACKEND`)

Both backends **always load**; `server.py:pick_manager()` routes each chat request
(the media endpoints always use `webapi_manager`). Both expose the same interface
(`.generate` / `.generate_stream`):

- `webapi_manager` — `gemini_pool.GeminiManager` over `gemini_webapi` cookies.
  Full-featured (chat, streaming, tools, vision, images, Veo video).
- `chrome_manager` — `chrome_backend.ChromeManager`: relays chat **and media** to a
  **Chrome extension** (in [`extension/`](extension/)) driving logged-in
  gemini.google.com tabs over a WebSocket (`/ws`, always registered via
  `register_ws(app)`). Auth = the browser's own session (no cookie
  harvesting/expiry). Since 2026-08-01 the hub is a **parallel tab pool**: every
  tab is a worker (per-tab IDs, strict `authuser` routing for images, retry-once
  on tab death, self-reloading extension, stale-tab auto-refresh, **per-account
  media lock** — Gemini web runs max ONE image gen per account — and **media
  account auto-failover** with quota cooldowns, env-tunable via
  `GEMINI_MEDIA_{ATTEMPT_TIMEOUT,QUOTA_COOLDOWN,EXCLUDE_AUTHUSERS}`). See devlog
  `claude_2026-08-01-parallel-tab-pool.md`.

`config.BACKEND` is a **preference**: `auto` (default — extension for chat AND
images when a tab is connected, else cookies; vision/video always cookies),
`webapi` (always cookies), `chrome` (always extension). Images via the extension
are the only reliable byte-path — Google 403s every server-side download of
`lh3 gg-dl` URLs (session-locked); the extension grabs bytes in-page and the
server serves them from `/files/`. Why an extension not injection:
page CSP blocks a page-context WS to localhost; a content script's isolated world
isn't governed by it. Test: `tests/chrome_backend_test.py` (19 checks incl.
auto-routing).

## Architecture (narrow-waist)

- `gemini_openai/config.py` — **single source of truth** for settings, cookie
  extraction (`get_cookies`, `get_full_jar`), model-name → `Model` mapping, and
  `BACKEND` selection.
- `gemini_openai/gemini_pool.py` — one shared, auto-refreshing `GeminiClient`
  (`manager`), re-inits on `AuthError`. The `webapi` backend.
- `gemini_openai/chrome_backend.py` — the `chrome` backend: `ChromeManager` +
  WebSocket hub + `/ws` route, same manager interface as `gemini_pool`.
- `gemini_openai/account.py` — Google multi-login: `/u/N/` endpoint routing +
  full-cookie-jar auth. Patches `gemini_webapi` via `sys.modules` (see devlog:
  the `utils.get_access_token` name resolves to a function, not the module).
- `gemini_openai/openai_schemas.py` — OpenAI request/response models + message
  flattening (`flatten_messages`, round-trips tool_calls/tool results).
- `gemini_openai/tools.py` — **emulated** function calling: `build_tools_prompt`
  injects schemas; `parse_tool_calls` extracts them back (multi-strategy +
  `json_repair`); code kept out of JSON via placeholder tokens. Design from
  deep-research (00bx/gemini-web-proxy, LiteLLM, vLLM, LocalAI) — see devlog.
- `gemini_openai/server.py` — FastAPI app: chat (stream+non-stream, tool calls),
  models, images, videos, `/files`.
- `gemini_openai/video.py` — Veo video: prime conversation → raw StreamGenerate
  (video inner flags) → poll `read_chat` for the download URL → download.
  Async job store behind the API. See devlog `claude_2026-07-08-0045-*`.
- `gemini_openai/video_bridge.py` — **browser bridge** for the video download:
  the `usercontent.google.com` host needs a per-host, per-account `OSID` only the
  browser mints, so a server GET 403s. Opt-in via `GEMINI_CDP_URL`: drives a
  logged-in Chrome over CDP (minimal `websockets` client) to `fetch()` the bytes
  in-page and return them keyed by job_id (parallel-safe). See the video devlog.

## State

- **Working & tested:** chat (stream + non-stream), **tool/function calling**
  (emulated, incl. streaming + round-trip), `/v1/models`, vision input,
  multi-turn, OpenAI SDK drop-in, image generation, multi-account auth,
  **Veo video** (`/v1/videos/generations`, async job + poll).
- **Video caveat:** per-account daily quota. Video only works as a follow-up
  turn in a primed conversation; `inner[49]` must NOT be set (turn counter →
  error 1053). Full detail in the video devlog.
- **Video download:** the `usercontent.google.com` MP4 host needs a per-host,
  per-account browser `OSID` (server GET → 403; same for `gemini_webapi`). Solved
  by the opt-in CDP browser bridge (`GEMINI_CDP_URL`, `video_bridge.py`); without
  it the job returns the browser-playable `download_url`. Real MP4 produced &
  verified end-to-end (`media/bridge_proof_fox.mp4`, 2.3 MB, valid ftyp/moov/mdat).

## Conventions

- `uv` for deps; empty `__init__.py`. Test scripts live in `tools/` (run with
  `PYTHONPATH=. uv run --active python tools/<x>.py`).
- Media generation has per-account daily quotas.
