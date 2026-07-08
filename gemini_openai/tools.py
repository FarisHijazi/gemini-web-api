"""Prompt-engineered ("poor man's") OpenAI tool/function calling.

The Gemini web app has no native tool-calling API, so we emulate it — the
well-established pattern used by LiteLLM (`add_function_to_prompt`), LocalAI,
vLLM's prompt parsers, and the near-identical `00bx/gemini-web-proxy`:

  request  → render the OpenAI `tools` JSON-Schemas into a system prompt that
             tells Gemini to reply with `{"tool_calls":[{"name",...,"arguments"}]}`
  response → parse that back out (multi-strategy, with json_repair) into the
             OpenAI `tool_calls` shape (arguments as a JSON-encoded *string*),
             finish_reason="tool_calls".

Key reliability techniques (from the research):
  - forbid raw code inside the JSON; put code in fenced blocks referenced by
    placeholder tokens, substituted back after parsing (keeps JSON valid);
  - multiple parse fallbacks (bracket-depth JSON → json_repair → regex);
  - if tool-call-like text is present but nothing parses, don't leak it into
    `content` (the dominant failure mode) — callers re-ask / treat as no-call.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

import json_repair

# Placeholder tokens: the model is told to put code in fenced blocks and
# reference them here instead of inlining multi-line code into JSON (which
# reliably corrupts it). We substitute the real code back after parsing.
PH_WRITE = "USE_CODE_BLOCK_ABOVE"
PH_OLD = "USE_OLD_CODE_ABOVE"
PH_NEW = "USE_NEW_CODE_ABOVE"


# --------------------------------------------------------------------------- #
# Request side: render tools into a system prompt
# --------------------------------------------------------------------------- #
def build_tools_prompt(tools: list[dict[str, Any]], tool_choice: Any = None) -> str:
    """Render OpenAI `tools` into an instruction block for Gemini."""
    lines: list[str] = []
    for t in tools:
        fn = t.get("function", t) if isinstance(t, dict) else {}
        name = fn.get("name", "")
        if not name:
            continue
        desc = (fn.get("description") or "").strip()
        params = fn.get("parameters") or {"type": "object", "properties": {}}
        lines.append(f"- {name}: {desc}\n  parameters (JSON Schema): {json.dumps(params)}")
    tool_list = "\n".join(lines)

    # tool_choice handling: "required"/{"type":"function",...} forces a call.
    forced = ""
    if tool_choice == "required":
        forced = "\nYou MUST call at least one tool in this turn."
    elif isinstance(tool_choice, dict):
        fname = (tool_choice.get("function") or {}).get("name")
        if fname:
            forced = f"\nYou MUST call the `{fname}` tool in this turn."

    return f"""# TOOLS

You can call the following tools. When you decide to use one or more tools,
reply with a SINGLE fenced ```json block containing ONLY this object and nothing
else before or after it:

```json
{{"tool_calls": [{{"name": "<tool_name>", "arguments": {{ ... }}}}]}}
```

Rules:
- `arguments` must be a JSON object matching that tool's parameter schema.
- You may return multiple tool calls in the `tool_calls` array (they run together).
- If you do NOT need a tool, reply normally in plain text with no json block.
- ⛔ NEVER put multi-line code or file contents directly inside the JSON —
  it breaks the JSON. Instead put the code in a SEPARATE fenced code block
  ABOVE the json block, and reference it with a placeholder string:
    • for a file's full content, use "{PH_WRITE}"
    • for an edit's old text, use "{PH_OLD}"; for the new text, use "{PH_NEW}"
  (If there are two code blocks, the first is old/content and the second is new.)

Available tools:
{tool_list}{forced}
"""


# --------------------------------------------------------------------------- #
# Response side: extract tool calls
# --------------------------------------------------------------------------- #
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_CODE_FENCE_RE = re.compile(r"```(?:[\w+-]*)\n(.*?)```", re.DOTALL)
_OBJ_RE = re.compile(r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*(\{.*?\})\s*\}', re.DOTALL)


def _code_blocks(text: str, exclude: str) -> list[str]:
    """Fenced code blocks (excluding the tool_calls json block)."""
    blocks = []
    for m in _CODE_FENCE_RE.finditer(text):
        body = m.group(1)
        if '"tool_calls"' in body or body.strip() == exclude.strip():
            continue
        blocks.append(body.rstrip("\n"))
    return blocks


def _loads(s: str) -> Any:
    """Strict json first, then json_repair (fixes missing quotes/commas/etc)."""
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return json_repair.loads(s)


def _bracket_match(text: str, open_ch: str, close_ch: str, start: int) -> str | None:
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_tool_calls(response: str) -> list[dict[str, Any]] | None:
    """Extract `[{"name","arguments":{...}}, ...]` from model output.

    Returns None when there is no tool call. Multi-strategy and forgiving.
    """
    cleaned = response.replace("\\_", "_")

    # Strategy 1: an object with a "tool_calls" array — bracket-depth match.
    key = cleaned.find('"tool_calls"')
    if key != -1:
        arr_start = cleaned.find("[", key)
        if arr_start != -1:
            blob = _bracket_match(cleaned, "[", "]", arr_start)
            if blob:
                try:
                    arr = _loads(blob)
                    calls = _normalize(arr)
                    if calls:
                        return _apply_code_blocks(calls, _code_blocks(cleaned, blob))
                except Exception:  # noqa: BLE001
                    pass

    # Strategy 2: a fenced ```json block containing the object.
    for m in _FENCE_RE.finditer(cleaned):
        body = m.group(1).strip()
        if '"tool_calls"' not in body and '"name"' not in body:
            continue
        try:
            obj = _loads(body)
        except Exception:  # noqa: BLE001
            continue
        arr = obj.get("tool_calls") if isinstance(obj, dict) else obj
        calls = _normalize(arr)
        if calls:
            return _apply_code_blocks(calls, _code_blocks(cleaned, body))

    # Strategy 3: any bare {"name":...,"arguments":{...}} objects.
    matches = _OBJ_RE.findall(cleaned)
    if matches:
        calls = []
        for name, args_str in matches:
            try:
                args = _loads(args_str)
            except Exception:  # noqa: BLE001
                args = {}
            calls.append({"name": name, "arguments": args if isinstance(args, dict) else {}})
        if calls:
            return _apply_code_blocks(calls, _code_blocks(cleaned, ""))

    return None


def _normalize(arr: Any) -> list[dict[str, Any]]:
    if not isinstance(arr, list):
        return []
    out = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or (item.get("function") or {}).get("name")
        args = item.get("arguments")
        if args is None:
            args = (item.get("function") or {}).get("arguments", {})
        if isinstance(args, str):
            try:
                args = _loads(args)
            except Exception:  # noqa: BLE001
                args = {}
        if name:
            out.append({"name": name, "arguments": args if isinstance(args, dict) else {}})
    return out


def _apply_code_blocks(calls: list[dict[str, Any]], blocks: list[str]) -> list[dict[str, Any]]:
    """Replace placeholder tokens in arguments with real fenced code blocks."""
    for c in calls:
        args = c.get("arguments", {})
        if not isinstance(args, dict):
            continue
        for k, v in list(args.items()):
            if v == PH_WRITE and len(blocks) >= 1:
                args[k] = blocks[0]
            elif v == PH_OLD and len(blocks) >= 1:
                args[k] = blocks[0]
            elif v == PH_NEW and len(blocks) >= 2:
                args[k] = blocks[1]
            elif v == PH_NEW and len(blocks) >= 1:
                args[k] = blocks[-1]
    return calls


# --------------------------------------------------------------------------- #
# Build the OpenAI response tool_calls shape
# --------------------------------------------------------------------------- #
def to_openai_tool_calls(parsed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI shape: id, type:function, function{name, arguments(JSON *string*)}."""
    out = []
    for i, tc in enumerate(parsed):
        out.append({
            "id": f"call_{uuid.uuid4().hex[:22]}",
            "type": "function",
            "index": i,
            "function": {
                "name": tc.get("name"),
                "arguments": json.dumps(tc.get("arguments", {}), ensure_ascii=False),
            },
        })
    return out


def strip_tool_json(text: str) -> str:
    """Remove any tool-call json/fences so leftover prose can be shown as content."""
    out = _FENCE_RE.sub("", text)
    return out.strip()
