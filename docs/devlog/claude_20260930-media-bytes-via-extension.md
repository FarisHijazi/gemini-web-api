# Media bytes via the extension, generation via cookies

**2026-09-30.** Supersedes the `media` backend mode this PR first added.

## What happened

With the extension connected, `media` mode sent image and video jobs to the tab,
which drives Gemini's UI and watches the DOM for the result. Both timed out in
the live test:

- video: 600s, "did not render in time"
- image: 430s, no reply

Gemini had in fact made the media. `read_chat` on the job's conversation
("Paper Boat on Pond") showed "Your video is ready!" with a video URL. In a
background tab, the `<video>` element never mounted and the image never
decoded.

## The fix

Generation stays on the cookie backend, which is reliable (~70–105s per video)
and already yields the URL. A connected tab now does only the one thing the
server can't, downloading the bytes with the browser's own session:

- `ChromeManager.fetch_bytes(url)` sends a `{"type":"fetch"}` job. It touches no
  DOM, so it shares a busy tab and doesn't take the media lock.
- `content.js` fetches in-page first, because the usercontent video host serves
  CORS to gemini.google.com. If that fails, `background.js` does it (`gcb-fetch`):
  `lh3` images send no CORS headers, and a service worker with host permissions
  is exempt.
- `video.download_video` tries the tab first, then the CDP bridge, then a direct
  GET.
- `server._local_url` does the same for images, asking for `=s2048-rj`. That's
  `gemini_webapi`'s own full-size suffix; the bare URL is a 512px preview.

The `media` mode was then identical to `webapi` plus a connected tab, so it was
removed. The launcher runs `GEMINI_BACKEND=webapi`. Only `chrome` mode still
generates media in the tab.

## Verified live (:8100, buzamahmooza@gmail.com, u/0)

- Video: completed in 70s. `/files/vid_….mp4` is 5.6 MB, ftyp, confirmed twice.
- Image: `/files/….jpg` is a 1408×768 JPEG, the requested watercolor lighthouse.
- Chat: still on cookies ("OK").
- `pytest tests/`: 45 passed, including the new `media_fetch_test.py`.
  `tests/chrome_backend_test.py` all pass.
