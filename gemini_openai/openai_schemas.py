"""OpenAI-compatible request/response schemas and message conversion helpers."""

from __future__ import annotations

import base64
import binascii
import json
import time
import uuid
from typing import Any

import httpx
from pydantic import BaseModel


def _safe_json(s: Any) -> Any:
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return {}

# --------------------------------------------------------------------------- #
# Request schemas (subset of OpenAI chat.completions we support)
# --------------------------------------------------------------------------- #
class ChatMessage(BaseModel):
    role: str
    # content is either a plain string or a list of parts (OpenAI vision format)
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    # tool-calling round-trip fields (assistant emits tool_calls; tool role replies)
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    # tool / function calling (emulated via prompt engineering — see tools.py)
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any | None = None  # "auto" | "none" | "required" | {"type":"function",...}
    # accepted but ignored (no upstream equivalent); present for SDK compatibility
    top_p: float | None = None
    n: int | None = None
    stop: Any | None = None
    user: str | None = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "model": "gemini-3-flash",
                "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
                "stream": False,
            }
        }
    }


# --------------------------------------------------------------------------- #
# Message flattening
# --------------------------------------------------------------------------- #
def _decode_image_part(url: str) -> bytes | None:
    """Turn an OpenAI image_url into raw bytes (data: URL or http(s) URL)."""
    if url.startswith("data:"):
        try:
            _, b64 = url.split(",", 1)
            return base64.b64decode(b64)
        except (ValueError, binascii.Error):
            return None
    if url.startswith(("http://", "https://")):
        try:
            r = httpx.get(url, timeout=30, follow_redirects=True)
            r.raise_for_status()
            return r.content
        except Exception:  # noqa: BLE001
            return None
    return None


def flatten_messages(messages: list[ChatMessage]) -> tuple[str, list[bytes]]:
    """Collapse an OpenAI message list into (prompt, image_files).

    OpenAI clients send the full conversation each call, so we render a plain
    transcript. A single user turn is sent as-is; multi-turn history is labeled.
    Image parts are extracted as bytes to pass to gemini_webapi's `files=`.
    """
    files: list[bytes] = []
    turns: list[tuple[str, str]] = []  # (role, text)

    for m in messages:
        text_parts: list[str] = []
        if isinstance(m.content, str):
            text_parts.append(m.content)
        elif isinstance(m.content, list):
            for part in m.content:
                ptype = part.get("type")
                if ptype == "text":
                    text_parts.append(part.get("text", ""))
                elif ptype == "image_url":
                    iu = part.get("image_url", {})
                    url = iu.get("url") if isinstance(iu, dict) else iu
                    if url:
                        data = _decode_image_part(url)
                        if data:
                            files.append(data)
        text = "\n".join(tp for tp in text_parts if tp)

        # Round-trip tool calls / results back into the transcript so the model
        # sees its own prior tool invocations and their results.
        if m.role == "assistant" and m.tool_calls:
            rendered = [
                {
                    "name": (tc.get("function") or {}).get("name"),
                    "arguments": _safe_json((tc.get("function") or {}).get("arguments", "{}")),
                }
                for tc in m.tool_calls
            ]
            tc_text = json.dumps({"tool_calls": rendered}, ensure_ascii=False)
            text = f"{text}\n{tc_text}".strip() if text else tc_text
        elif m.role == "tool":
            name = m.name or "tool"
            text = f"Tool Result ({name}):\n{text}"

        turns.append((m.role, text))

    # Fast path: a single user message (optionally with a system message).
    non_empty = [(r, t) for r, t in turns if t.strip() or r == "user"]
    if len([t for t in non_empty if t[0] not in ("system",)]) == 1:
        sys_txt = "\n\n".join(t for r, t in turns if r == "system" and t.strip())
        usr_txt = "\n\n".join(t for r, t in turns if r not in ("system",) and t.strip())
        prompt = f"{sys_txt}\n\n{usr_txt}".strip() if sys_txt else usr_txt
        return prompt, files

    # Multi-turn: render a labeled transcript ending with an Assistant cue.
    label = {"system": "System", "user": "User", "assistant": "Assistant", "tool": "Tool"}
    lines: list[str] = []
    for r, t in turns:
        if not t.strip():
            continue
        lines.append(f"{label.get(r, r.capitalize())}: {t}")
    lines.append("Assistant:")
    return "\n\n".join(lines), files


# --------------------------------------------------------------------------- #
# Response builders
# --------------------------------------------------------------------------- #
def _rid() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def completion_response(
    model: str,
    content: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict:
    message: dict[str, Any] = {"role": "assistant"}
    if tool_calls:
        message["content"] = None
        message["tool_calls"] = tool_calls
        finish = "tool_calls"
    else:
        message["content"] = content
        finish = "stop"
    return {
        "id": _rid(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def chunk(model: str, rid: str, created: int, delta: dict, finish: str | None = None) -> dict:
    return {
        "id": rid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
