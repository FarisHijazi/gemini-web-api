# Reverse-engineering gemini.google.com into an OpenAI-compatible API

**Date:** 2026-07-07
**Goal:** Unofficial OpenAI-compatible API for gemini.google.com/app — Gemini
chat first (with streaming), then Veo video.

See [`../../README.md`](../../README.md) for usage. This log records *how* the
protocol was reverse-engineered and the non-obvious findings, so future changes
can be reasoned about.

## 1. The protocol (captured from live traffic via a fetch/XHR interceptor)

- **Endpoint:** `POST https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate`
- **Query:** `bl` (build label), `f.sid` (session id), `hl`, `_reqid`, `rt=c`
- **Body (form-urlencoded):** `f.req=[null,"<inner-json-string>"]` + `at=<SNlM0e token>`
- **Inner json:** `[[ "<prompt>",0,... ], ["en"], ["","",""], ...]` — a ~69-slot
  array where specific indices are feature flags.
- **Page globals** (scraped from the app HTML): `SNlM0e` = access token (`at`),
  `cfb2h` = `bl` build label, `FdrFJe` = `f.sid`.

This matches the [`gemini_webapi`](https://github.com/HanaokaYuzu/Gemini-API)
library exactly, so we use it as the protocol engine (v2.0.0 ships current
**Gemini 3** models) and build the OpenAI layer on top.

## 2. Auth — the two hard parts

### Full cookie jar (not just `__Secure-1PSID`)
`gemini_webapi` (and most clones) authenticate with only `__Secure-1PSID` +
`__Secure-1PSIDTS`. That reaches **only the default Google account (`u/0`)**.

### Multi-login `/u/N/` accounts
When several Google accounts are signed in, each is selected by a `/u/N/` URL
prefix; there is **no separate `__Secure-1PSID` per account** (the two present
differ only by TLD `.google.com` vs `.google.com.sa`). The non-default account's
session lives in the shared `SID`/`SAPISID`/`OSID` cookies.

**Solution** (`gemini_openai/account.py`):
1. Send the **entire `.google.com` (path=`/`) cookie jar**, not two cookies.
2. Prefix every endpoint (`INIT`, `GENERATE`, `BATCH_EXEC`) with `/u/N/`.
3. Verified: full jar + `/u/5/` → a real answer from the intended account.

### Gotcha: Python import shadowing
`gemini_webapi/utils/__init__.py` re-exports `get_access_token` as a **function**,
so `import gemini_webapi.utils.get_access_token as m` binds the *function*, not
the module — patching `m.Endpoint` silently no-ops. Fix: resolve the real module
via `sys.modules["gemini_webapi.utils.get_access_token"]`. This cost hours; the
symptom was the token fetch hitting `/app` while `batchexecute` correctly hit
`/u/5/`.

## 3. OpenAI compatibility layer

- `POST /v1/chat/completions`: OpenAI messages flattened to a transcript, sent to
  `generate_content`. Streaming (`stream:true`) is **true token streaming**: it
  consumes `gemini_webapi`'s `generate_content_stream` and forwards each upstream
  `text_delta` live as an SSE `chat.completion.chunk` delta (verified: TTFT well
  below total, deltas arrive over the generation window). Tool-calling requests
  still buffer — the complete reply is needed to parse the `tool_calls` JSON.
  (Originally this was faked by slicing a buffered reply; replaced once the
  library's streaming API was wired in — see the true-streaming devlog.)
- `GET /v1/models`, `POST /v1/images/generations` (image gen works).
- Model aliases: `gpt-4*`→pro, `gpt-3.5-turbo`→flash, plus `gemini-3-*` names.
- Verified end-to-end with the official `openai` Python SDK: non-stream, stream,
  multi-turn memory, vision input, model routing.

## 4. Veo video — trigger cracked, retrieval blocked

Diffed live "Create image" vs "Create video" requests. The differentiators
(the library **hardcodes the image values**, which is why naive attempts always
returned images):

| inner index | image | video |
|---|---|---|
| `mc[9]` (message_content[9]) | absent | `[null,…,[[null,null,null,1]]]` (media tool) |
| `inner[49]` | `14` | **`11`** (generation type) |
| `inner[17]` | `[[0]]` | **`[[1]]`** |
| `inner[54]` | absent | `[]` |
| `inner[55]` (aspect ratio) | absent | `[[16]]` (16:9) |
| model header `[…]` capabilities | `[4,5,6,8]` | `[4,5,6,8]` (same) |

The model header is **identical** for image and video — the selector is the
inner payload. `gemini_openai/video.py` injects these via a scoped `json`
(orjson) proxy on `gemini_webapi.client` that only rewrites the one 69-slot
payload while a `contextvar` is set.

With these flags the request **routes to Veo** (confirmed: behaviour changed from
"image returned" to async "no CID / still generating"). Video is delivered
**asynchronously** over ~1–3 min. Two remaining problems:

1. `generate_content` (one-shot) has no chat CID to recover the async result →
   "No CID found to recover". Using a `ChatSession` fixes the CID, but then…
2. A faithful raw replay of the full payload gets Google **error `1053`**
   (`BardErrorInfo [1053]`) — a server-side rejection. Adding the deep-research-
   style nonces (`inner[3]`, `inner[4]`, `inner[79]=3`, `inner[91]=0`) did not
   clear it. The browser succeeds with the same visible fields, so a subtle
   field/header/nonce difference remains.

**Next step for video:** capture one *complete* successful browser video request
(every non-null inner slot with full values + all request headers) and replay it
byte-for-byte to isolate the `1053` cause; then implement async retrieval by
reading the long-lived `StreamGenerate` stream (the video URL arrives in a late
chunk — there is no separate poll RPC; only one `StreamGenerate` fires per video).

## 5. Environment notes

- Media generation (image/video) has **per-account daily quotas**. During
  development the personal account and one work account were quota-exhausted
  ("Limit resets …"); a fresh account (`GEMINI_AUTHUSER=6`) had quota and
  produced real videos in the browser.
- `pkill -f main.py` will kill the launching shell (its own command line matches)
  — kill the server by port instead: `fuser -k 8100/tcp`.
