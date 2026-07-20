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
from .gemini_pool import manager
from .openai_schemas import (
    ChatCompletionRequest,
    chunk,
    completion_response,
    flatten_messages,
    _rid,
)

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
    return {"status": "ok"}


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

    output = await manager.generate(
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
        content, tool_calls, prompt = await _run_generation(req)
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
            async for out in manager.generate_stream(
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
async def images_generations(body: dict, _=Depends(check_key)):
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt required")
    model = config.resolve_model(body.get("model"))
    instruction = f"Generate an image: {prompt}"
    output = await manager.generate(instruction, model=model, temporary=True)
    images = getattr(output, "images", []) or []
    if not images:
        raise HTTPException(status_code=502, detail="model returned no image")
    data = [{"url": img.url, "revised_prompt": getattr(img, "title", None)} for img in images if getattr(img, "url", None)]
    if not data:
        raise HTTPException(status_code=502, detail="model returned no image url")
    return {"created": int(time.time()), "data": data}


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
    model = config.resolve_model(body.get("model") or "gemini-3-pro")
    files = None  # optional image-to-video could pass reference frames here
    job_id = video_mod.create_job(manager, prompt, model, files)
    return JSONResponse(_job_view(job_id, video_mod.JOBS[job_id], request), status_code=202)


@app.get("/v1/videos/generations/{job_id}")
async def videos_get(job_id: str, request: Request, _=Depends(check_key)):
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
