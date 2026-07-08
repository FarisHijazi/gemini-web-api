# Emulated OpenAI tool/function calling ("poor man's" tool calling)

**Date:** 2026-07-07
**Goal:** Make agentic coding clients (opencode, Cline, Cursor, Aider) work
against the Gemini web app, which has no native function-calling API.

Design was driven by a `/deep-research` run (25 claims, all 3-0 verified). See
[@../../README.md](../../README.md) §"Tool / function calling" for usage.

## Approach (well-established pattern)

Prompt-inject the tool schemas, have the model emit a JSON tool-call block, parse
it back to the OpenAI shape. Confirmed by:
- **`00bx/gemini-web-proxy`** — near-identical project (Gemini web app +
  gemini_webapi, built for OpenCode). Directly transferable design.
- **LiteLLM** `add_function_to_prompt=True`; **LocalAI** centralizes extraction
  in the proxy; **vLLM** formalizes it as per-model `extract_tool_calls()` /
  `extract_tool_calls_streaming()` parsers → the parse-back layer must match the
  exact syntax the model emits.

## Implementation (`gemini_openai/tools.py`)

**Request** — `build_tools_prompt(tools, tool_choice)` renders each
`{name, description, parameters}` and instructs Gemini to reply with:
```json
{"tool_calls": [{"name": "<tool>", "arguments": { ... }}]}
```
`tool_choice: "required"` / `{"type":"function",...}` adds a "you MUST call…" line.

**Code-in-JSON problem** (the biggest practical issue for coding agents): multi-
line code inlined into JSON reliably corrupts it. Fix (from 00bx): forbid inline
code; the model puts code in separate fenced blocks and references placeholder
tokens `USE_CODE_BLOCK_ABOVE` / `USE_OLD_CODE_ABOVE` / `USE_NEW_CODE_ABOVE`,
which `_apply_code_blocks()` substitutes with the real code after parsing.

**Response** — `parse_tool_calls()` is multi-strategy (a model deviates often):
1. locate `"tool_calls"`, bracket-depth-match the `[...]`, `json.loads` →
   `json_repair.loads` fallback;
2. any fenced ```json block with `tool_calls`/`name`;
3. regex for bare `{"name":…,"arguments":{…}}`.
`to_openai_tool_calls()` emits `{id, type:"function", index, function:{name,
arguments}}` with **arguments as a JSON-encoded string** (OpenAI requires this;
returning a dict breaks clients). `finish_reason="tool_calls"`.

**Dominant failure mode defended against** (documented across mlx-lm, sglang,
Ollama, LM Studio): when a tool call is emitted in an unrecognized format the
raw text leaks into `message.content` while `tool_calls` stays empty. Mitigations
here: forgiving multi-strategy parser; and when tools were requested and a call
is parsed, prose outside the JSON is dropped rather than leaked as content.

**History round-trip** (`flatten_messages`): assistant `tool_calls` are
re-serialized as `{"tool_calls":[…]}` and `tool`-role results as
`Tool Result (name): …` so the model sees the full loop.

**Streaming** — the buffered reply is parsed, then each tool call is emitted as a
`tool_calls` delta followed by `finish_reason:"tool_calls"`.

## Tested (`tools/tools_e2e.py`, live against Gemini u/6)

1. model decides to call → `finish_reason:"tool_calls"`, `get_weather({"city":"Tokyo"})`, args a JSON string ✓
2. full round-trip: submit tool result → final answer reflects it ("18°C and clear") ✓
3. no tool needed → plain text, `finish_reason:"stop"` ✓
4. streaming tool call ✓

Plus 7 offline parser unit tests (`scratchpad/test_parser.py`): fenced,
prose+fence, parallel, json_repair, placeholder-sub, edit old/new, plain-text.

## Reliability caveats (from research)

Prompt-emulated tool calling is **less token-efficient and less reliable than a
native API** (llama.cpp's own docs; an arXiv study found some prompted models
>70% hallucination). It works for agentic coding but budget for occasional parse
retries. Future hardening available: re-ask on schema-validation failure
(Instructor's reask pattern) and few-shot examples in the tools prompt.
