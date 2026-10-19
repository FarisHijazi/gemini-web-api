# Free-plan animated clips and account triage (2026-10-19)

Context: GitTok renders PR videos from Veo clips through this server.

- **Free-plan accounts return a GIF, not a video.** The turn text is "Because you're on a free plan, I generated a GIF for you instead." `read_chat` parses no video and no image for it; the clip is an attachment entry `[_, 1, "animated_image.gif", "https://lh3.googleusercontent.com/gg/..."]` inside the turn's nested JSON strings. `video.find_animated_image` walks the decoded `LIST_CONVERSATION_TURNS` response for it (structure walk, no phrase matching), and the poller returns it when no real video is present. A real video always wins.
- **Download.** lh3 403s server-side like usercontent, so the extension fetches it. The bare URL redirects to a 288x512 preview; `=s0` gives the native 360x640. ffmpeg re-encodes it to a silent H.264 MP4 at the usual `/files/<job>.mp4`, and the job view reports `kind: "animated_image"` (`"video"` otherwise).
- **`fetch_bytes` falls back to any tab.** The service worker sends every signed-in account's cookies and the URL's `authuser` picks the owner, so a free account without its own Gemini tab still downloads.
- **`TOS_PENDING` kills generation.** The log line `Account status: TOS_PENDING` was followed by "The original request may have been silently aborted by Google" on every attempt. Dismissing the terms notice on `gemini.google.com/u/N/app` fixed it.
- **Stale quota flags.** Abort and ToS failures were recorded in `~/.cache/gemini-web-api/quota.json` as exhaustion, which hid working accounts. After clearing it, u/0 and u/1 produced real 720x1280 10 s Veo clips with audio; u/6 only replies "upgrade your subscription" (neither video nor GIF).
- The timeout error now names every profile tried, not just the one that succeeded.

Verified: 61 unit tests (5 new: GIF result, GIF beats stop signal, video beats GIF, nested-JSON detection on the live shape, non-GIF/foreign hosts ignored). `find_animated_image` found the URL in a captured live response, and the `=s0` URL loads as 360x640 in the browser with `authuser`. A full live GIF job was not completed because u/5 was temporarily rate-limited ("getting a lot of requests").
