"""End-to-end tool-calling test via the OpenAI SDK against the live server."""

import json

from openai import OpenAI

client = OpenAI(base_url="http://localhost:8100/v1", api_key="x")

WEATHER = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "City name"}},
            "required": ["city"],
        },
    },
}

print("== 1. model decides to call the tool ==")
r = client.chat.completions.create(
    model="gemini-3-flash",
    messages=[{"role": "user", "content": "What's the weather in Tokyo right now? Use the tool."}],
    tools=[WEATHER],
)
msg = r.choices[0].message
print("finish_reason:", r.choices[0].finish_reason)
print("tool_calls:", msg.tool_calls)
assert r.choices[0].finish_reason == "tool_calls", "expected tool_calls finish"
assert msg.tool_calls and msg.tool_calls[0].function.name == "get_weather"
args = json.loads(msg.tool_calls[0].function.arguments)  # must be a JSON string
print("parsed args:", args)
assert args.get("city", "").lower().startswith("tokyo")

print("\n== 2. full round-trip: submit tool result, get final answer ==")
tc = msg.tool_calls[0]
r2 = client.chat.completions.create(
    model="gemini-3-flash",
    messages=[
        {"role": "user", "content": "What's the weather in Tokyo? Use the tool."},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}},
        ]},
        {"role": "tool", "tool_call_id": tc.id, "name": "get_weather",
         "content": json.dumps({"city": "Tokyo", "temp_c": 18, "condition": "clear"})},
    ],
    tools=[WEATHER],
)
final = r2.choices[0].message.content
print("final answer:", repr(final))
assert final and ("18" in final or "clear" in final.lower()), "expected the tool result reflected"

print("\n== 3. no tools needed -> plain text ==")
r3 = client.chat.completions.create(
    model="gemini-3-flash",
    messages=[{"role": "user", "content": "Say the single word: hello"}],
    tools=[WEATHER],
)
print("finish_reason:", r3.choices[0].finish_reason, "content:", repr(r3.choices[0].message.content))
assert r3.choices[0].finish_reason == "stop"

print("\n== 4. streaming tool call ==")
acc_name, acc_args = "", ""
for ev in client.chat.completions.create(
    model="gemini-3-flash",
    messages=[{"role": "user", "content": "Weather in Berlin? call the tool"}],
    tools=[WEATHER], stream=True,
):
    d = ev.choices[0].delta
    if d.tool_calls:
        for t in d.tool_calls:
            if t.function and t.function.name:
                acc_name += t.function.name
            if t.function and t.function.arguments:
                acc_args += t.function.arguments
    fr = ev.choices[0].finish_reason
    if fr:
        print("stream finish_reason:", fr)
print("streamed tool:", acc_name, acc_args)
assert acc_name == "get_weather"

print("\nALL TOOL-CALLING TESTS PASSED")
