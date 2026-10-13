"""Unit tests for GeminiManager's per-account model selection.

Google rolled an "Auto"-only picker out to some accounts (2026-10-13): their
registry holds just `gemini-auto`, so asking for `gemini-3-flash` raised
`ValueError: Unknown model name` and every request on that profile was a 500.
"""
import asyncio

from gemini_openai.gemini_pool import GeminiManager


class FakeClient:
    def __init__(self, known):
        self.known = known
        self.calls = []

    def resolve_model(self, name):
        if name not in self.known:
            raise ValueError(f"Unknown model name: '{name}'")
        return self.known[name]

    async def generate_content(self, prompt, **kwargs):
        self.calls.append(kwargs)
        return "ok"

    async def generate_content_stream(self, prompt, **kwargs):
        self.calls.append(kwargs)
        yield "chunk"


def manager_with(client):
    m = GeminiManager()
    m._client = client
    return m


def test_model_the_account_lacks_falls_back_to_google_default():
    client = FakeClient({"gemini-auto": "AUTO"})
    asyncio.run(manager_with(client).generate("hi", model="gemini-3-flash"))
    assert client.calls == [{"model": None}]


def test_model_the_account_has_is_resolved_to_its_own_entry():
    client = FakeClient({"gemini-3-flash": "FLASH"})
    asyncio.run(manager_with(client).generate("hi", model="gemini-3-flash", temporary=True))
    assert client.calls == [{"model": "FLASH", "temporary": True}]


def test_stream_falls_back_too():
    client = FakeClient({"gemini-auto": "AUTO"})

    async def go():
        return [c async for c in manager_with(client).generate_stream("hi", model="gemini-3-pro")]

    assert asyncio.run(go()) == ["chunk"]
    assert client.calls == [{"model": None}]


def test_non_string_model_passes_through_untouched():
    # The video path hands the library a raw model dict (custom header).
    client = FakeClient({})
    spec = {"model_name": "x", "model_header": {}}
    asyncio.run(manager_with(client).generate("hi", model=spec))
    assert client.calls == [{"model": spec}]
