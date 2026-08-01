# Chrome-extension backend: image + video verified end-to-end (and the generate-vs-search bug)

**Date:** 2026-07-31 ~21:15
**Author:** Claude Code, at the user's request ("I need the image + video stuff especially to
use the chrome extension … make sure it works").
**Scope:** finishing + live-verifying the Chrome-extension media path built earlier this session
(see the two-backend architecture in `README.md` / `CLAUDE.md`). Code lives in `extension/` and
`gemini_openai/{chrome_backend,server}.py`.

## Result — both modalities captured live through the real extension

| Modality | Endpoint | Job | Output | Proof |
|---|---|---|---|---|
| Image | `POST /v1/images/generations` | sync | `1024×559` PNG, 1,511,952 B | red bicycle vs blue wall — matched prompt |
| Video | `POST /v1/videos/generations` (async job) | `processing`→`completed` ~60s | `1280×720` h264+aac MP4, 10.0s, 2,791,982 B | valid ISO Media, ffprobe-clean |

Both responses carried `"backend":"chrome"` — i.e. they went through the extension → local WS →
server, **not** the cookie path. The video was fetched in-page from
`https://contribution.usercontent.google.com/download?...` with `credentials:'include'` and the
`*.usercontent.google.com` host-permission — the OSID-free download the whole extension pivot was for.

## The real bug found this session: Gemini *web-searches* instead of *generating*

Earlier "empty/timeout" failures were **two** distinct problems stacked on a genuinely-degraded
Gemini backend (which recovered mid-session):

1. **Framing skipped the generate command exactly when it was needed.** Old logic:
   `/(image|picture|photo)/.test(prompt) ? prompt : "Generate an image: "+prompt`. A prompt like
   *"…sharp focus, photo"* was sent **raw**, and Gemini Flash answered with an **Unsplash stock
   photo + "prompt tips for Midjourney/DALL·E"** — a web search, not a generation. Proven by the
   chat title ("Prompt Refinement Tips") and the response text ("Source: Unsplash").
   **Fix:** always issue an explicit *"Generate an image yourself with your image generation tool;
   do not search the web and do not return a stock photo: …"* command (video analog too).

2. **The finder accepted search thumbnails.** Gemini renders a searched image inside `<single-image>`
   too, as a cross-origin `encrypted-tbn0.gstatic.com` / `images.unsplash.com` thumbnail. Grabbing
   that tainted the canvas → `Tainted canvases may not be exported`. **Fix:** `isGeneratedImg()` only
   accepts `blob:` / `data:` / `googleusercontent.com` srcs and rejects gstatic/unsplash thumbnails.

3. **Image grab is now dual-path** (`grabImage` is async): canvas for same-origin `blob:`/`data:`
   (fast, taint-free — the normal Imagen case), authenticated `fetch()` fallback for a genuinely
   generated `googleusercontent` URL. Search thumbnails never reach it (rejected in #2).

### Discriminator that matters going forward
- **Generated** image = `blob:https://gemini.google.com/…` (Imagen, same-origin, canvas-exportable).
- **Searched** image = `gstatic.com` / `unsplash.com` thumbnail → must be rejected.
- Verified manually: with the strong framing, the kitten prompt produced a `1024×559` `blob:` image
  (generated); without it, an Unsplash thumbnail (searched).

## Verification method (Gemini was mid-outage, so this was done carefully)
- Confirmed the outage was **Google-side**: a manual, non-extension "say ready" took 53s+ with no
  reply; recovered later in the session.
- Manually validated the framing fix in-page (blob generated image) **before** trusting it.
- The successful live image+video runs used a **generate-friendly prompt** (no "photo/image" word)
  so even the *currently-loaded* (pre-fix) extension prepends "Generate an image:" and generates.

## Open / caveats
- **The framing + finder fixes in `content.js` are NOT yet loaded into the running extension**
  (can't reload it via automation — `chrome://` is blocked). They're validated by manual in-page
  test; the live pipeline runs above worked with the old content.js + a generate-friendly prompt.
  **Reload the unpacked extension** to get robustness for *any* prompt (incl. ones containing
  "photo/image") and the gstatic/unsplash rejection.
- **All of this is uncommitted** in the working tree (`extension/`, `chrome_backend.py`, `server.py`,
  `config.py`, `tests/`). The `gemini-web-api` **systemd service runs the old *published git* code
  via uvx**, so it does not have the chrome backend and currently crash-loops on the busy :8100.
  To make it permanent: commit + push, free :8100, restart the service — or keep the dev server.
