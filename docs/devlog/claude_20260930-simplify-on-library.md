# Simplify: lean on gemini-webapi instead of re-deriving it

A `/simplify` pass (reuse / simplification / efficiency / altitude) over the
code written for the 2.1 migration, video, and account pinning. Everything
below was verified live: chat, image, and a video that completed in 105 s.

## Removed

- **Raw-response scraping in the video poller.** It wrapped the private
  `client._batch_execute`, regexed the capture for the MP4 URL, and decoded
  batchexecute escapes. `read_chat` already parses finished videos:
  `turn.model_output.videos[0].url` (`client.py:2164-2181`). Checked live that
  it returns the byte-identical URL before switching.
- **`_is_quota_failure` matching the word "quota".** Leftover from the deleted
  phrase list: an explicit stop ("You're out of videos for now") no longer
  contained it, so profile failover silently stopped happening. Now a typed
  `VideoStopped` counts as exhaustion.
- 8 `tools/` scripts calling `generate_video()` or carrying the deleted
  hand-built request; dead `model`/`cid` parameters; the `chat.metadata`
  guard for the removed raw request; a redundant `bool(obj)`.
- `config.py`: the thinking-tier function + helper + 9-line table became one
  flag and a comprehension; `account_of` path parsing became `relpath`;
  `Local State` read once; the `_announced` global became `functools.cache`.

## Kept, on purpose (review findings skipped)

- **Stop reason from the log sink**, not parsed output. A stopped turn and a
  finalized-but-still-rendering turn both parse as "text, no video"; the
  library's warning is the only thing that distinguishes them.
- **Video model as an explicit header dict.** Tried the model name: the live
  run failed with `Unknown model name: 'gemini-3-pro-advanced'. Available
  registered models: gemini-flash-lite, gemini-flash, gemini-pro` — the
  account's registry omits advanced tiers. `build_model_header` differs between
  2.0 `(id, tail)` and 2.1 `(id, tail, number)`, so the string stays, now a
  constant instead of a one-caller function (verified byte-identical).
- The double cookie-store read in `get_cookies`/`get_full_jar`: predates this
  work, and only costs ~100-300 ms at init.

## Launcher (`~/bin/gemini-web-api-server`, untracked)

Root cause of the old `descendants()` PPID walk: `( cd X && nohup cmd & )`
backgrounds the whole `&&` list, so `$!` was a forked subshell and python its
child. Now it `cd`s first and backgrounds only `nohup .venv/bin/python main.py`
after `uv sync --frozen`: pidfile == listener, and a plain `kill` frees the
port (verified with a stop/start cycle). Also fixed: it read `GEMINI_PORT`
while the server binds `GEMINI_API_PORT`; start now polls every 0.5 s and bails
as soon as the process dies instead of waiting out 40 × 1 s.
