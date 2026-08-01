"""OpenAI-compatible FastAPI server backed by the Gemini web app.

Endpoints:
  GET  /v1/models
  POST /v1/chat/completions        (stream + non-stream, vision input, media output)
  POST /v1/images/generations      (Gemini/Imagen image generation)
  POST /v1/videos/generations      (Veo3 video generation, async job + poll)
  GET  /v1/videos/generations/{id} (poll a video job)
  GET  /health
"""

from __future__ import annotations

import asyncio
import json
import time

import os

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse

from . import config, tools as tools_mod, video as video_mod
from .openai_schemas import (
    ChatCompletionRequest,
    chunk,
    completion_response,
    flatten_messages,
    _rid,
)

# ---- backends: BOTH load; the server can serve either at runtime ------------ #
# Both expose the same manager interface (generate / generate_stream):
#   webapi_manager — cookie/CDP gemini_webapi library (chat + images + Veo video)
#   chrome_manager — relay to a Chrome extension driving a logged-in tab (chat)
# config.BACKEND is a PREFERENCE, not a hard switch:
#   "auto"   (default) — use the extension for chat when a tab is connected,
#                        otherwise fall back to the cookie backend
#   "webapi"           — always the cookie backend
#   "chrome"           — always the extension for chat
# Media generation (images/video) always uses the cookie backend (the extension
# can't produce them), so a single server does chat-via-extension AND cookie media.
from .chrome_backend import hub as _chrome_hub, manager as chrome_manager, register_ws
from .gemini_pool import manager as webapi_manager


def pick_manager(has_files: bool = False):
    """Choose the chat backend for this request."""
    mode = config.BACKEND
    if mode == "webapi":
        return webapi_manager
    if mode == "chrome":
        return chrome_manager
    # auto: prefer the extension when a tab is connected, but the extension can't
    # do vision, so route file/image inputs to the cookie backend.
    if _chrome_hub.online() and not has_files:
        return chrome_manager
    return webapi_manager


def active_backend_name() -> str:
    mode = config.BACKEND
    if mode == "auto":
        return "chrome" if _chrome_hub.online() else "webapi"
    return mode


def media_via_chrome() -> bool:
    """Whether image/video generation should go through the extension.

    Yes when chrome is forced, or auto-mode has a tab connected. This is the whole
    point of the extension for media: the browser downloads the bytes natively, so
    there's no usercontent/OSID download problem to solve.
    """
    if config.BACKEND == "chrome":
        return True
    return config.BACKEND == "auto" and _chrome_hub.online()


def _save_media_b64(b64: str, ext: str) -> tuple[str, int]:
    """Decode base64 media into MEDIA_DIR; return (filename, byte length)."""
    import base64
    import binascii
    import os
    import uuid

    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=502, detail="extension returned an undecodable media payload")
    os.makedirs(video_mod.MEDIA_DIR, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(video_mod.MEDIA_DIR, name), "wb") as f:
        f.write(raw)
    return name, len(raw)


# Chrome video jobs (the extension blocks minutes while Veo renders, so run it in
# the background and let clients poll, matching the webapi video API shape).
_CHROME_VIDEO_JOBS: dict[str, dict] = {}
_CHROME_JOB_TTL = 3600.0  # drop finished jobs after an hour so the dict can't grow forever


def _prune_chrome_jobs() -> None:
    now = time.time()
    stale = [
        jid
        for jid, j in _CHROME_VIDEO_JOBS.items()
        if j.get("status") in ("completed", "failed")
        and now - j.get("created", now) > _CHROME_JOB_TTL
    ]
    for jid in stale:
        _CHROME_VIDEO_JOBS.pop(jid, None)


def _norm_aspect(body: dict) -> str:
    """Normalise the requested video aspect ratio to "16:9" or "9:16".

    Accepts `aspect_ratio` ("16:9"/"9:16"/"landscape"/"portrait") or an OpenAI-style
    `size` ("1280x720" -> landscape, "720x1280" -> portrait). Defaults to landscape.
    """
    raw = str(body.get("aspect_ratio") or body.get("size") or "").strip().lower()
    if any(k in raw for k in ("9:16", "portrait", "vertical")) or raw in ("720x1280", "1080x1920"):
        return "9:16"
    if "x" in raw and raw.replace("x", "").isdigit():
        w, _, h = raw.partition("x")
        if w.isdigit() and h.isdigit() and int(h) > int(w):
            return "9:16"
    return "16:9"


async def _run_chrome_video(job_id: str, prompt: str, aspect: str = "16:9") -> None:
    job = _CHROME_VIDEO_JOBS[job_id]
    job["status"] = "processing"
    try:
        media, _text, served = await chrome_manager.generate_media(
            prompt, "video", timeout=610.0, aspect=aspect
        )
        job["authuser"] = served
        vid = next((m for m in media if m.get("kind") == "video" and m.get("b64")), None)
        if not vid:
            raise RuntimeError("extension returned no video")
        ext = "mp4" if "mp4" in (vid.get("mime") or "") else "webm"
        name, n = _save_media_b64(vid["b64"], ext)
        job.update(status="completed", file=name, bytes=n)
    except Exception as e:  # noqa: BLE001
        job.update(status="failed", error=str(e))

app = FastAPI(
    title="Gemini OpenAI-compatible API",
    version="1.0.0",
    description=(
        "Unofficial OpenAI-compatible API backed by the gemini.google.com web app.\n\n"
        "- **POST /v1/chat/completions** — streaming + non-streaming, vision input\n"
        "- **GET /v1/models** — list available Gemini models\n"
        "- **POST /v1/images/generations** — image generation\n"
        "- **POST /v1/videos/generations** — Veo video generation (async job + poll)\n\n"
        "Point any OpenAI SDK at `http://localhost:8100/v1`."
    ),
)


register_ws(app)  # /ws endpoint the Chrome extension connects to (always available)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")


# --------------------------------------------------------------------------- #
# Auth (optional bearer token)
# --------------------------------------------------------------------------- #
def check_key(authorization: str | None = Header(default=None)) -> None:
    if not config.API_KEY:
        return
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if token != config.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _media_markdown(output) -> str:
    """Markdown for any generated media (images/videos) on a ModelOutput."""
    parts: list[str] = []
    for img in getattr(output, "images", []) or []:
        url = getattr(img, "url", None)
        title = getattr(img, "title", None) or "image"
        if url:
            parts.append(f"![{title}]({url})")
    for vid in getattr(output, "videos", []) or []:
        url = getattr(vid, "url", None)
        title = getattr(vid, "title", None) or "video"
        if url:
            parts.append(f"[{title}]({url})")
    return "\n\n".join(parts).strip()


def render_output(output) -> str:
    """ModelOutput -> markdown text including any generated media URLs."""
    text = (output.text or "").strip()
    media = _media_markdown(output)
    return "\n\n".join(p for p in (text, media) if p).strip()


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
@app.get("/v1/models")
async def list_models(_=Depends(check_key)) -> dict:
    now = int(time.time())
    data = [
        {"id": name, "object": "model", "created": now, "owned_by": "google-gemini"}
        for name in config.list_public_models()
    ]
    return {"object": "list", "data": data}


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "backend_mode": config.BACKEND,          # auto | webapi | chrome
        "active_backend": active_backend_name(),  # which one handles chat right now
        "extension_connected": _chrome_hub.online(),
    }


@app.get("/v1/status")
async def status() -> dict:
    """Effective server configuration.

    The CLI/doctor runs in a different shell than the server (often a systemd
    service), so its own env vars say nothing about how the server is actually
    configured. This reports the truth. No secrets are included.
    """
    return {
        "status": "ok",
        "backend_mode": config.BACKEND,
        "active_backend": active_backend_name(),
        "extension_connected": _chrome_hub.online(),
        "tabs": _chrome_hub.tabs(),              # connected tab pool (parallel workers)
        # accounts skipped by media failover (seconds since their quota failure)
        "media_quota_cooldown": {
            a: round(time.monotonic() - t) for a, t in chrome_manager.quota_bad.items()
        },
        "authuser": config.AUTHUSER or "0",
        "authuser_fallbacks": [
            p.strip() for p in os.getenv("GEMINI_AUTHUSER_FALLBACKS", "").split(",") if p.strip()
        ],
        "cdp_bridge": bool(config.CDP_URL),      # video download + cookie harvest
        "video_timeout_s": video_mod.VIDEO_TIMEOUT,
        "media_dir": video_mod.MEDIA_DIR,
        "api_key_required": bool(config.API_KEY),
    }


_pending_reload: dict = {"at": 0.0}


@app.get("/v1/extension/pending-reload")
async def extension_pending_reload() -> dict:
    """Backup reload channel: the extension's service worker polls this every
    minute (chrome.alarms). A request younger than 90s means "reload now"; it is
    cleared on first read so the reload fires once."""
    pending = (time.time() - _pending_reload["at"]) < 90
    if pending:
        _pending_reload["at"] = 0.0
    return {"reload": pending}


@app.post("/v1/extension/reload")
async def extension_reload(_=Depends(check_key)) -> dict:
    """Broadcast a reload command to every connected Gemini tab.

    Each content script relays it to the extension's service worker, which calls
    chrome.runtime.reload(); on reload the worker re-injects content scripts into
    all open Gemini tabs, so the whole cycle needs no human. Old script instances
    shut themselves down via the generation guard.
    """
    _pending_reload["at"] = time.time()
    n = 0
    for conn in list(_chrome_hub.conns.values()):
        try:
            await conn.send({"type": "reload"})
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return {"sent": n, "note": "tabs reconnect within ~15s (or <=60s via alarm poll)"}


# --------------------------------------------------------------------------- #
# Chat completions
# --------------------------------------------------------------------------- #
async def _run_generation(req: ChatCompletionRequest):
    """Run one turn. Returns (content_text, tool_calls|None, prompt).

    When the request carries `tools`, the schemas are injected into the prompt
    and the model's reply is parsed back into OpenAI tool_calls (emulated
    function calling — see tools.py).
    """
    prompt, files = flatten_messages(req.messages)
    model = config.resolve_model(req.model)
    if not prompt.strip() and not files:
        raise HTTPException(status_code=400, detail="empty prompt")

    if req.tools and req.tool_choice != "none":
        prompt = tools_mod.build_tools_prompt(req.tools, req.tool_choice) + "\n\n" + prompt

    output = await pick_manager(has_files=bool(files)).generate(
        prompt,
        files=files or None,
        model=model,
        temporary=True,  # keep the user's Gemini history clean
    )
    text = render_output(output)

    if req.tools and req.tool_choice != "none":
        parsed = tools_mod.parse_tool_calls(text)
        if parsed:
            tool_calls = tools_mod.to_openai_tool_calls(parsed)
            # Any prose outside the tool json is dropped (don't leak it as content
            # to agentic clients — the dominant failure mode).
            return None, tool_calls, prompt
    return text, None, prompt


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest, _=Depends(check_key)):
    model_name = req.model or "gemini-3-flash"

    if not req.stream:
        try:
            content, tool_calls, prompt = await _run_generation(req)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 -- surface upstream/backend errors informatively
            raise HTTPException(status_code=502, detail=str(e))
        return JSONResponse(
            completion_response(
                model_name, content, _est_tokens(prompt),
                _est_tokens(content or ""), tool_calls=tool_calls,
            )
        )

    async def event_stream():
        rid = _rid()
        created = int(time.time())
        yield _sse(chunk(model_name, rid, created, {"role": "assistant"}))

        # Tool calls can't be streamed incrementally: we need the complete reply
        # to parse the {"tool_calls":[…]} JSON, so this path stays buffered.
        if req.tools and req.tool_choice != "none":
            try:
                content, tool_calls, _ = await _run_generation(req)
            except HTTPException as e:
                yield _sse(chunk(model_name, rid, created, {"content": f"[error: {e.detail}]"}, "stop"))
                yield "data: [DONE]\n\n"
                return
            except Exception as e:  # noqa: BLE001
                yield _sse(chunk(model_name, rid, created, {"content": f"[error: {e}]"}, "stop"))
                yield "data: [DONE]\n\n"
                return
            if tool_calls:
                for tc in tool_calls:
                    yield _sse(chunk(model_name, rid, created, {"tool_calls": [tc]}))
                    await asyncio.sleep(0)
                yield _sse(chunk(model_name, rid, created, {}, "tool_calls"))
            else:
                for piece in _chunk_text(content or ""):
                    yield _sse(chunk(model_name, rid, created, {"content": piece}))
                    await asyncio.sleep(0)
                yield _sse(chunk(model_name, rid, created, {}, "stop"))
            yield "data: [DONE]\n\n"
            return

        # True streaming: forward each upstream text_delta as it arrives.
        prompt, files = flatten_messages(req.messages)
        model = config.resolve_model(req.model)
        if not prompt.strip() and not files:
            yield _sse(chunk(model_name, rid, created, {"content": "[error: empty prompt]"}, "stop"))
            yield "data: [DONE]\n\n"
            return

        final_output = None
        try:
            async for out in pick_manager(has_files=bool(files)).generate_stream(
                prompt, files=files or None, model=model, temporary=True
            ):
                final_output = out
                delta = out.text_delta or ""
                if delta:
                    yield _sse(chunk(model_name, rid, created, {"content": delta}))
        except HTTPException as e:
            yield _sse(chunk(model_name, rid, created, {"content": f"[error: {e.detail}]"}, "stop"))
            yield "data: [DONE]\n\n"
            return
        except Exception as e:  # noqa: BLE001
            yield _sse(chunk(model_name, rid, created, {"content": f"[error: {e}]"}, "stop"))
            yield "data: [DONE]\n\n"
            return

        # Any generated media (images/videos) isn't part of text_delta — append it.
        media = _media_markdown(final_output) if final_output else ""
        if media:
            yield _sse(chunk(model_name, rid, created, {"content": "\n\n" + media}))
        yield _sse(chunk(model_name, rid, created, {}, "stop"))
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _chunk_text(text: str, size: int = 24):
    """Yield word-ish chunks so the client sees incremental deltas."""
    if not text:
        return
    words = text.split(" ")
    buf = ""
    for w in words:
        buf = f"{buf} {w}" if buf else w
        if len(buf) >= size:
            yield buf + " "
            buf = ""
    if buf:
        yield buf


# --------------------------------------------------------------------------- #
# Image generation (OpenAI images API shape)
# --------------------------------------------------------------------------- #
@app.post("/v1/images/generations")
async def images_generations(body: dict, request: Request, _=Depends(check_key)):
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt required")

    # Preferred path: generate in a logged-in tab and grab the bytes natively.
    # Optional body field "authuser" routes the job to a tab on that Google
    # multi-login account (image quota is per-account; see /v1/status "tabs").
    if media_via_chrome():
        try:
            media, _text, served = await chrome_manager.generate_media(
                prompt, "image", timeout=430.0,
                authuser=(str(body["authuser"]) if body.get("authuser") is not None else None),
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"chrome image generation failed: {e}")
        base = str(request.base_url).rstrip("/")
        data = []
        for m in media:
            if m.get("kind") != "image" or not m.get("b64"):
                continue
            ext = "png" if "png" in (m.get("mime") or "") else "jpg"
            name, _n = _save_media_b64(m["b64"], ext)
            data.append({"url": f"{base}/files/{name}"})
        if not data:
            raise HTTPException(status_code=502, detail="extension returned no image")
        return {"created": int(time.time()), "data": data, "backend": "chrome",
                "authuser": served}

    # Cookie backend (image generation via the gemini_webapi library).
    manager = webapi_manager
    model = config.resolve_model(body.get("model"))
    instruction = f"Generate an image: {prompt}"

    # Image generation also has a per-account daily quota: an exhausted profile
    # replies with text ("come back tomorrow…") and no image. Walk the configured
    # fallback profiles the same way video jobs do.
    original = str(config.AUTHUSER or "0")
    candidates = video_mod._profile_candidates()
    tried: list[str] = []
    last_text = ""
    for prof in candidates:
        if prof != str(config.AUTHUSER or "0"):
            await video_mod._switch_profile(manager, prof)
        tried.append(prof)
        output = await manager.generate(instruction, model=model, temporary=True)
        data = [
            {"url": img.url, "revised_prompt": getattr(img, "title", None)}
            for img in (getattr(output, "images", []) or [])
            if getattr(img, "url", None)
        ]
        if data:
            return {
                "created": int(time.time()),
                "data": data,
                "authuser": prof,
                "quota_exhausted": tried[:-1] or None,
            }
        last_text = (output.text or "").strip()

    # Nothing worked — restore the starting profile and explain why.
    if str(config.AUTHUSER or "0") != original:
        try:
            await video_mod._switch_profile(manager, original)
        except Exception:  # noqa: BLE001
            pass
    raise HTTPException(
        status_code=502,
        detail=(
            f"no image from profile(s) {','.join(tried)} — usually the daily image "
            f"quota. Model said: {last_text[:200] or '(nothing)'}"
        ),
    )


# --------------------------------------------------------------------------- #
# Video generation (Veo3) — async job + poll
# --------------------------------------------------------------------------- #
def _job_view(job_id: str, job: dict, request: Request) -> dict:
    out = {
        "id": job_id,
        "object": "video.generation",
        "status": job["status"],
        "prompt": job.get("prompt"),
    }
    if job.get("authuser") is not None:
        out["authuser"] = job["authuser"]          # profile that served this job
    if job.get("quota_exhausted"):
        out["quota_exhausted"] = job["quota_exhausted"]  # profiles skipped en route
    if job["status"] == "completed":
        out["download_url"] = job.get("download_url")  # Google usercontent URL
        if job.get("file"):
            base = str(request.base_url).rstrip("/")
            out["url"] = f"{base}/files/{job_id}.mp4"
            out["bytes"] = job.get("bytes")
        if job.get("download_error"):
            out["download_error"] = job["download_error"]
    if job.get("error"):
        out["error"] = job["error"]
    return out


@app.post("/v1/videos/generations")
async def videos_generations(body: dict, request: Request, _=Depends(check_key)):
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt required")

    # Reference frames for image-to-video. The chrome/extension backend cannot
    # attach files (chrome_backend.generate raises on `files`), so any request
    # carrying images must go down the webapi path below.
    files = body.get("images") or body.get("files") or None
    if isinstance(files, str):
        files = [files]

    # Preferred path: generate in the tab; the browser holds the video bytes so we
    # skip the whole usercontent/OSID download bridge.
    if media_via_chrome() and not files:
        import uuid

        _prune_chrome_jobs()
        aspect = _norm_aspect(body)
        job_id = f"cvid_{uuid.uuid4().hex[:16]}"
        _CHROME_VIDEO_JOBS[job_id] = {
            "status": "queued", "prompt": prompt, "aspect": aspect, "created": time.time()
        }
        asyncio.create_task(_run_chrome_video(job_id, prompt, aspect))
        return JSONResponse(
            _chrome_job_view(job_id, _CHROME_VIDEO_JOBS[job_id], request), status_code=202
        )

    model = config.resolve_model(body.get("model") or "gemini-3-pro")
    aspect_int = 9 if _norm_aspect(body) == "9:16" else 16
    job_id = video_mod.create_job(webapi_manager, prompt, model, files, aspect_int)
    return JSONResponse(_job_view(job_id, video_mod.JOBS[job_id], request), status_code=202)


def _chrome_job_view(job_id: str, job: dict, request: Request) -> dict:
    out = {
        "id": job_id,
        "object": "video.generation",
        "status": job["status"],
        "prompt": job.get("prompt"),
        "aspect": job.get("aspect"),
        "backend": "chrome",
    }
    if job["status"] == "completed" and job.get("file"):
        base = str(request.base_url).rstrip("/")
        out["url"] = f"{base}/files/{job['file']}"
        out["bytes"] = job.get("bytes")
    if job.get("error"):
        out["error"] = job["error"]
    return out


@app.get("/v1/videos/generations/{job_id}")
async def videos_get(job_id: str, request: Request, _=Depends(check_key)):
    cjob = _CHROME_VIDEO_JOBS.get(job_id)
    if cjob:
        return _chrome_job_view(job_id, cjob, request)
    job = video_mod.JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_view(job_id, job, request)


@app.get("/files/{name}")
async def serve_file(name: str):
    # basic traversal guard
    if "/" in name or ".." in name:
        raise HTTPException(status_code=400, detail="bad name")
    path = os.path.join(video_mod.MEDIA_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path)


def run() -> None:
    """Console-script entry point (`gemini-web-api`). Starts the uvicorn server."""
    import uvicorn

    uvicorn.run("gemini_openai.server:app", host=config.HOST, port=config.PORT, log_level="info")
