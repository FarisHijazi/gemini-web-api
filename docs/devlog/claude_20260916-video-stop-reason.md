# Video: report why Gemini stopped, instead of guessing

## Symptom

A video job sat at `processing` for the full 600 s and then failed with:

> timed out after 600s on profile(s) 0 — this almost always means the daily
> video quota is exhausted there.

The message was a guess, and it had already been wrong once (it fired while the
account had ~24 000 credits left; the real cause then was a malformed payload).

## What was actually happening

The server log carried the answer the whole time, once per poll:

```
WARNING | gemini_webapi.components.chat_mixin:read_chat:212 -
  [read_chat] Gemini generation was interrupted/stopped for 'c_854056614f6da444'.
  Reason: You're out of videos for now. Videos will be available again on ...
```

The poller had its own detector, a list of English phrases:

```python
_QUOTA_MARKERS = ("come back tomorrow", "can't generate more videos", "can't create more videos")
```

Google now words it "You're out of videos for now", so nothing matched, and the
poller kept polling a turn that was never going to produce a video.

## Fix

Drop the phrase list. `gemini_webapi` already detects a stopped turn and logs
the server's verbatim reason, so key on *its* signal via a scoped loguru sink
and surface the reason unchanged. Google can reword the message freely; the
library's detection does not depend on the wording.

The URL check now runs **before** the stop check, so a video already in hand
always wins over a stop signal arriving in the same round.

## Why keying on the other log lines would break it

`read_chat` also logs "still working on the response" and "successfully
finalized the response". Finalized looks like a natural stop condition and is
**not** one: inside a single *successful* video run the log held

```
4 x [read_chat] Gemini has successfully finalized the response
5 x [read_chat] Gemini is still working on the response
```

The text answer finalizes while Veo is still rendering; the video URL only
appears in a later poll. Ending the poll on "finalized" would abort every
successful generation. Only a URL or an explicit stop may end the poll.

Conversely the stop warning is a trustworthy signal: across every successful run
that day it appeared **0** times, and 73 times once the daily video allowance
ran out.

## Tests

`tests/video_poll_test.py` — a live happy-path run needs unused daily video
quota, so the ordering rule that protects it is covered with a fake client
instead: URL wins over a stop signal, the verbatim reason is raised, progress
chatter is not mistaken for a stop, and the log sink is removed even when the
poll raises.

## Related

- Payload construction handed back to the library: `claude_20260915-account-pin-and-webapi-2.1-shim.md`
- Video *bytes* (not the URL) still need `GEMINI_CDP_URL`; the usercontent host
  enforces a per-account OSID. Unchanged here.
