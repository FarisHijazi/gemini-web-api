"""Smoke test: init gemini_webapi with extracted cookies and do one round-trip."""

import asyncio
import sys

from gemini_webapi import GeminiClient

sys.path.insert(0, "tools")
from extract_cookies import extract  # noqa: E402


async def main() -> None:
    creds = extract()
    assert creds, "no cookies found"
    client = GeminiClient(creds["__Secure-1PSID"], creds["__Secure-1PSIDTS"])
    await client.init(timeout=60, auto_refresh=False)
    print("init OK")

    r = await client.generate_content(
        "Reply with exactly this and nothing else: PONG_42", model="gemini-3-flash"
    )
    print("MODEL=flash TEXT:", repr(r.text[:200]))

    # multi-turn via chat
    chat = client.start_chat(model="gemini-3-pro")
    r2 = await chat.send_message("My name is Zebra. Remember it.")
    print("PRO turn1:", repr(r2.text[:120]))
    r3 = await chat.send_message("What name did I tell you? One word.")
    print("PRO turn2:", repr(r3.text[:120]))

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
