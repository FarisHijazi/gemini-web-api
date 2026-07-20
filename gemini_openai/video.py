"""Veo3 video generation for the Gemini web app.

The web app triggers Veo video generation by adding a "tool" flag inside the
StreamGenerate payload's message_content array (index 9):

    message_content = [prompt, 0, None, files, None, None, 0, None, None,
                       [None, None, None, None, None, None, [[None, None, None, 1]]]]

gemini_webapi doesn't expose this, so we inject it with a narrowly-scoped proxy
around the `json` (orjson) reference used inside gemini_webapi.client. The proxy
only rewrites the one payload shape (`inner_req_list`, a 69-element list) and
only while the `_video_ctx` contextvar is set — every other serialization is
passed straight through untouched. This lets us reuse the library's existing
response parser and video-polling logic (GeneratedVideo.save handles the 206
"still generating" retries).
"""

from __future__ import annotations

import contextvars

import gemini_webapi.client as _gclient

# message_content[9] value that selects the "generate visual media" tool.
# Reverse-engineered from live gemini.google.com "Create videos"/"Create image"
# traffic — this same flag is used for both image and video generation.
VIDEO_TOOL = [None, None, None, None, None, None, [[None, None, None, 1]]]

# What actually selects VIDEO over image is the model header's capability array:
# regular Pro sends [4]; video sends [4,5,6,8] plus mode field 3. Model id
# "e6fa609c3fa255c0" is gemini-3-pro-advanced. Captured live from a working
# video generation request (Veo, "Create video" mode, Landscape 16:9).
def _video_header(uuid_val: str) -> str:
    # index 8 caps [4,5,6,8] + index 14 mode 3 + index 16 session uuid selects
    # video. The uuid is the same one used in x-goog-ext-525005358-jspb.
    return (
        '[1,null,null,null,"e6fa609c3fa255c0",null,null,0,[4,5,6,8],'
        f'null,null,2,null,null,3,null,"{uuid_val}"]'
    )


VIDEO_MODEL = {
    "model_name": "gemini-3-video",
    "model_header": {"x-goog-ext-525001261-jspb": _video_header("00000000-0000-0000-0000-000000000000")},
}

_video_ctx: contextvars.ContextVar[bool] = contextvars.ContextVar("gemini_video_mode", default=False)
# Aspect ratio code: 16 = 16:9 landscape (default), 9 = 9:16 portrait, 1 = 1:1.
_video_ctx_aspect: contextvars.ContextVar[int] = contextvars.ContextVar("gemini_video_aspect", default=16)


class _JsonProxy:
    """Transparent proxy over orjson that injects the video tool flag on demand."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def dumps(self, obj, *args, **kwargs):
        if (
            _video_ctx.get()
            and isinstance(obj, list)
            and len(obj) == 69
            and obj and isinstance(obj[0], list)
        ):
            mc = obj[0]
            while len(mc) < 10:
                mc.append(None)
            mc[9] = VIDEO_TOOL
            # The image-vs-video selectors (reverse-engineered by diffing live
            # "Create image" vs "Create video" traffic):
            #   inner[17] = [[1]] (video) vs [[0]] (image)   — the library hardcodes [[0]]
            #   inner[55] = [[aspect]] (16=16:9, 9=9:16, 1=1:1) — video only
            #   inner[54] = []                                — video only
            # NOTE: inner[49] is a per-conversation TURN COUNTER, NOT the video
            # selector. Setting an arbitrary value in a fresh turn causes Google
            # error 1053 — so we must NOT set it and let the library manage it.
            # Video also requires an existing conversation context (a primed
            # chat turn) — see generate_video().
            obj[17] = [[1]]
            obj[54] = []
            obj[55] = [[_video_ctx_aspect.get()]]
        return self._real.dumps(obj, *args, **kwargs)


def _install_proxy() -> None:
    if not isinstance(_gclient.json, _JsonProxy):
        _gclient.json = _JsonProxy(_gclient.json)


_install_proxy()


import asyncio  # noqa: E402
import json as _json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
import urllib.parse  # noqa: E402
import uuid  # noqa: E402

# Static message_content flags (see the JSON proxy above for the full rationale).
# We send the video request RAW (below) rather than through the library's
# generate_content, so we set the inner fields explicitly here.
DEFAULT_METADATA = ["", "", "", None, None, None, None, None, None, ""]
# Video model header: capabilities [4,5,6,8] enable media; the uuid is a fixed
# placeholder (the web app itself sends all-zeros here for the model header).
VIDEO_MODEL_HEADER = (
    '[1,null,null,null,"e6fa609c3fa255c0",null,null,0,[4,5,6,8],'
    'null,null,2,null,null,3,null,"00000000-0000-0000-0000-000000000000"]'
)
# The finished MP4 is served from a time-limited usercontent download URL.
_DL_RE = re.compile(
    r'https://[^"\\\s]*usercontent\.google\.com/download\?[^"\\\s]*filename=video\.mp4[^"\\\s]*'
)
_QUOTA_MARKERS = ("come back tomorrow", "can't generate more videos", "can't create more videos")


def _decode_escapes(blob: str) -> str:
    """Decode any-depth \\uXXXX (batchexecute double-escapes) and \\/."""
    return re.sub(r"\\+u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), blob).replace("\\/", "/")


def _build_video_inner(prompt: str, metadata, uid: str, aspect: int) -> list:
    mc = [prompt, 0, None, None, None, None, 0, None, None, VIDEO_TOOL]
    inner = [None] * 69
    inner[0] = mc
    inner[1] = ["en"]
    inner[2] = metadata               # existing-conversation context (required)
    inner[6] = [1]; inner[7] = 1; inner[10] = 1; inner[11] = 0
    inner[17] = [[1]]                 # video (image is [[0]])
    inner[18] = 0; inner[27] = 1; inner[30] = [4]; inner[41] = [1]
    inner[53] = 0; inner[54] = []
    inner[55] = [[aspect]]            # aspect ratio (16=16:9, 9=9:16, 1=1:1)
    inner[59] = uid; inner[61] = []; inner[68] = 2
    # NOTE: inner[49] (turn counter) is intentionally NOT set — an arbitrary
    # value there causes Google error 1053.
    return inner


async def _send_raw_video_request(client, metadata, prompt: str, aspect: int) -> str:
    """POST the video StreamGenerate over the library's session; return raw text.

    Returns quickly with a "Creating your video…" pending state (the finished
    video is fetched later via read_chat polling).
    """
    uid = str(uuid.uuid4()).upper()
    inner = _build_video_inner(prompt, metadata, uid, aspect)
    freq = _json.dumps([None, _json.dumps(inner)])
    endpoint = str(_gclient.Endpoint.GENERATE)  # already /u/N-prefixed by account.py
    params = {"hl": "en", "_reqid": str(uuid.uuid4().int % 900000 + 100000), "rt": "c",
              "bl": client.build_label}
    if getattr(client, "session_id", None):
        params["f.sid"] = client.session_id
    url = endpoint + "?" + urllib.parse.urlencode(params)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        "Origin": "https://gemini.google.com", "Referer": "https://gemini.google.com/",
        "X-Same-Domain": "1",
        "x-goog-ext-525001261-jspb": VIDEO_MODEL_HEADER,
        "x-goog-ext-525005358-jspb": f'["{uid}",1]',
    }
    r = await client.client.post(
        url, data={"at": client.access_token, "f.req": freq}, headers=headers, timeout=120
    )
    return r.text


async def _poll_video_url(client, cid: str, timeout: float, interval: float = 8.0) -> str:
    """Poll read_chat until the finished-video download URL appears."""
    captured: list[str] = []
    orig_be = client._batch_execute

    async def cap(payloads, *a, **k):
        r = await orig_be(payloads, *a, **k)
        try:
            captured.append(r.text)
        except Exception:  # noqa: BLE001
            pass
        return r

    client._batch_execute = cap
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    try:
        while loop.time() < deadline:
            captured.clear()
            try:
                await client.read_chat(cid, limit=3)
            except Exception:  # noqa: BLE001
                pass
            blob = "\n".join(captured)
            if any(m in blob for m in _QUOTA_MARKERS):
                raise RuntimeError("daily video generation quota exhausted for this account")
            urls = _DL_RE.findall(_decode_escapes(blob))
            if urls:
                return urls[0]
            await asyncio.sleep(interval)
    finally:
        client._batch_execute = orig_be
    raise TimeoutError("video did not finish generating in time")


async def generate_video_url(manager, prompt: str, aspect: int = 16, timeout: float = 300.0) -> dict:
    """Full pipeline → returns {"download_url", "cid"} for a finished video.

    1. Prime a conversation (video only works as a follow-up turn — a fresh
       first turn returns error 1053).
    2. Send the raw video request (async; returns a pending state).
    3. Poll read_chat until the finished-video download URL is available.
    """
    from .config import Model

    client = await manager.get()
    chat = client.start_chat(model=Model.BASIC_PRO)
    await chat.send_message("I want to create a video. Reply with just: READY")
    cid = chat.cid
    if not cid or not chat.metadata:
        raise RuntimeError("failed to open a conversation for video generation")

    await _send_raw_video_request(client, chat.metadata, prompt, aspect)
    url = await _poll_video_url(client, cid, timeout=timeout)
    return {"download_url": url, "cid": cid}


async def download_video(manager, url: str, dest: str, cid: str | None = None) -> int:
    """Download a finished-video URL to `dest`. Returns byte size.

    Two paths:
      1. If GEMINI_CDP_URL is set, fetch through a logged-in browser (the only
         way past the usercontent per-account OSID — see video_bridge.py).
      2. Otherwise a direct server-side GET, which works only if the host does
         not enforce OSID (it usually does → 403; caller surfaces download_url).
    """
    from . import config

    if config.CDP_URL:
        from . import video_bridge
        n = config.AUTHUSER or "0"
        # Any logged-in page of the right account works — the bridge mints the
        # per-host OSID itself by loading the URL in a <video> element.
        page_url = f"https://gemini.google.com/u/{n}/app"
        data = await video_bridge.fetch_video_bytes(config.CDP_URL, page_url, url)
        if b"ftyp" not in data[:64]:
            raise RuntimeError("bridge returned non-mp4 data")
        with open(dest, "wb") as f:
            f.write(data)
        return len(data)

    client = await manager.get()
    r = await client.client.get(url, headers={"Referer": "https://gemini.google.com/"}, timeout=180)
    if r.status_code != 200 or b"ftyp" not in r.content[:64]:
        raise RuntimeError(
            f"video download failed (HTTP {r.status_code}); the usercontent host "
            "requires a per-account browser OSID — set GEMINI_CDP_URL to enable "
            "the browser bridge, or use the returned download_url in a browser"
        )
    with open(dest, "wb") as f:
        f.write(r.content)
    return len(r.content)


# --------------------------------------------------------------------------- #
# Async job store: video generation takes minutes, so it runs as a background
# job that clients poll (OpenAI-style async media generation).
# --------------------------------------------------------------------------- #
MEDIA_DIR = os.getenv("GEMINI_MEDIA_DIR", "media")
VIDEO_TIMEOUT = float(os.getenv("GEMINI_VIDEO_TIMEOUT", "600"))  # seconds

# job_id -> {status, prompt, url, file, error, created}
JOBS: dict[str, dict] = {}


# Remember which profiles are out of quota, so the next job doesn't re-walk them.
# Discovering "everything is exhausted" costs timeout x N profiles (~26 min for
# six), which is far too slow to repeat per request.
_QUOTA_CACHE = os.path.expanduser("~/.cache/gemini-web-api/quota.json")
_QUOTA_TTL = float(os.getenv("GEMINI_QUOTA_CACHE_TTL", "10800"))  # 3h; quota is daily


def _quota_cache_read() -> dict[str, float]:
    try:
        with open(_QUOTA_CACHE) as f:
            data = _json.load(f)
        now = time.time()
        return {k: v for k, v in data.items() if isinstance(v, (int, float)) and v > now}
    except Exception:  # noqa: BLE001
        return {}


def mark_quota_exhausted(profile: str) -> None:
    """Record that `profile` is out of media quota (expires after the TTL)."""
    data = _quota_cache_read()
    data[str(profile)] = time.time() + _QUOTA_TTL
    try:
        os.makedirs(os.path.dirname(_QUOTA_CACHE), exist_ok=True)
        with open(_QUOTA_CACHE, "w") as f:
            _json.dump(data, f)
    except Exception:  # noqa: BLE001
        pass


def _profile_candidates() -> list[str]:
    """Google profiles to try, current one first.

    Media generation has a PER-ACCOUNT daily quota, so an exhausted profile makes
    the whole request fail even though other signed-in accounts still work. Set
    GEMINI_AUTHUSER_FALLBACKS="0,1,2,5" to let jobs roll over automatically.

    Profiles known to be out of quota are skipped — unless that would leave
    nothing to try, in which case we ignore the cache rather than hard-fail on
    stale data (quota may have reset).
    """
    from . import config

    cur = str(config.AUTHUSER or "0")
    raw = os.getenv("GEMINI_AUTHUSER_FALLBACKS", "").strip()
    order = [cur] if not raw else [cur] + [
        p.strip() for p in raw.split(",") if p.strip() and p.strip() != cur
    ]
    known_bad = _quota_cache_read()
    fresh = [p for p in order if p not in known_bad]
    if fresh:
        return fresh
    # Every profile is flagged. Don't re-walk them all (timeout x N ~ 26 min for
    # six) — probe just one, which is enough to notice the daily quota reset.
    return order[:1]


async def _switch_profile(manager, n: str) -> None:
    """Point the shared client at Google profile u/N (drops the cached client)."""
    import shutil

    from . import config
    from .account import apply_authuser

    await manager.reset()
    shutil.rmtree("/tmp/gemini_webapi", ignore_errors=True)
    config.AUTHUSER = n
    apply_authuser(n)


def _is_quota_failure(exc: BaseException) -> bool:
    """Quota exhaustion shows up either as an explicit message or as a stall.

    An exhausted account frequently never finishes generating rather than
    erroring cleanly, so a timeout counts as (probable) quota exhaustion.
    """
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    return "quota" in str(exc).lower()


async def _generate_with_failover(manager, job: dict, prompt: str):
    """Try each candidate profile until one produces a video."""
    from . import config

    original = str(config.AUTHUSER or "0")
    candidates = _profile_candidates()
    last_exc: BaseException | None = None
    try:
        for i, prof in enumerate(candidates):
            if prof != str(config.AUTHUSER or "0"):
                await _switch_profile(manager, prof)
            job["authuser"] = prof
            try:
                result = await asyncio.wait_for(
                    generate_video_url(manager, prompt, timeout=VIDEO_TIMEOUT),
                    timeout=VIDEO_TIMEOUT + 60,
                )
                job["tried_profiles"] = candidates[: i + 1]
                return result
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if _is_quota_failure(e):
                    mark_quota_exhausted(prof)
                if not _is_quota_failure(e) or i == len(candidates) - 1:
                    raise
                job.setdefault("quota_exhausted", []).append(prof)
    finally:
        # Leave the shared client on the profile that worked; if everything
        # failed, restore the original so chat isn't left on a random account.
        if last_exc is not None and job.get("authuser") != original and not job.get("file"):
            try:
                await _switch_profile(manager, original)
            except Exception:  # noqa: BLE001
                pass
    raise last_exc  # pragma: no cover


async def _run_job(manager, job_id: str, prompt: str, model, files):
    job = JOBS[job_id]
    job["status"] = "processing"
    try:
        result = await _generate_with_failover(manager, job, prompt)
        # The video is generated; expose its (valid, browser-playable) URL even
        # if the server-side fetch fails on the usercontent OSID auth wrinkle.
        job["download_url"] = result["download_url"]
        job["cid"] = result.get("cid")
        job["status"] = "completed"
        try:
            os.makedirs(MEDIA_DIR, exist_ok=True)
            path = os.path.join(MEDIA_DIR, f"{job_id}.mp4")
            job["bytes"] = await download_video(
                manager, result["download_url"], path, cid=result.get("cid")
            )
            job["file"] = path
        except Exception as e:  # noqa: BLE001
            # generation succeeded; only the server-side download step didn't
            job["download_error"] = f"{type(e).__name__}: {e}"
    except (asyncio.TimeoutError, TimeoutError):
        job["status"] = "failed"
        tried = job.get("tried_profiles") or _profile_candidates()
        job["error"] = (
            f"timed out after {VIDEO_TIMEOUT:.0f}s on profile(s) {','.join(tried)} — "
            "this almost always means the daily video quota is exhausted there. "
            "Set GEMINI_AUTHUSER_FALLBACKS to more profiles, or try again tomorrow."
        )
    except Exception as e:  # noqa: BLE001
        job["status"] = "failed"
        job["error"] = f"{type(e).__name__}: {e}"


def create_job(manager, prompt: str, model, files) -> str:
    job_id = "vid_" + uuid.uuid4().hex[:20]
    JOBS[job_id] = {"status": "queued", "prompt": prompt, "created": True}
    asyncio.create_task(_run_job(manager, job_id, prompt, model, files))
    return job_id
