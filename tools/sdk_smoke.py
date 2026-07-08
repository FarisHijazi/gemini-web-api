"""Drop-in test using the official OpenAI SDK against our local server."""

import base64

from openai import OpenAI

client = OpenAI(base_url="http://localhost:8100/v1", api_key="not-needed")

print("== models ==")
print([m.id for m in client.models.list().data][:4])

print("\n== non-stream ==")
r = client.chat.completions.create(
    model="gemini-3-flash",
    messages=[{"role": "user", "content": "Reply with exactly: SDK_OK"}],
)
print(r.choices[0].message.content)

print("\n== stream ==")
stream = client.chat.completions.create(
    model="gemini-3-flash",
    messages=[{"role": "user", "content": "List three fruits, comma separated."}],
    stream=True,
)
acc = ""
for ev in stream:
    d = ev.choices[0].delta.content or ""
    acc += d
print("streamed:", repr(acc.strip()))

print("\n== gpt-4 alias -> pro, multi-turn memory ==")
r = client.chat.completions.create(
    model="gpt-4",
    messages=[
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "My secret word is Falcon."},
        {"role": "assistant", "content": "Understood."},
        {"role": "user", "content": "Repeat my secret word, one word only."},
    ],
)
print("multi-turn:", repr(r.choices[0].message.content.strip()))

# 1x1 red PNG for a vision round-trip
RED_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
print("\n== vision input ==")
r = client.chat.completions.create(
    model="gemini-3-pro",
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What color is this image? One word."},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64," + base64.b64encode(RED_PNG).decode()},
                },
            ],
        }
    ],
)
print("vision:", repr(r.choices[0].message.content.strip()))
print("\nALL SDK TESTS DONE")
