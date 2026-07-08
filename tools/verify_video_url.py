"""Download the extracted video URL and confirm it's a real MP4."""

import asyncio
import os

os.environ["GEMINI_AUTHUSER"] = "6"
from gemini_openai.gemini_pool import manager  # noqa: E402

URL = ("https://lh3.googleusercontent.com/gg/AEir0wJxFLeQUhwAHxjHHqpKf3aRcGaJ0Yv07GBF4wg3NoktE7eQNeNUk9YXtJc2CxSS8qjqsoJazXn_UBXMa7GNpmR6PiGLAXmpF5DedyDQIgrCSKLqq_PrazgqhgLtd7C2y2PSn1YylLZ4DR3QuZ_rgajELhXATlLlXnS_Eg7_sAQQHbxKUkoy7UhMi1gdkCogPvaqlPWWdfX5TVK-sVaJd-bvFgEXNjk6vx9wsLGaV-ZJ5oBZfgsKhI3-77NccIuV0rVQvZSrY9Vugbg0_xrD_tBUMxQNF2zn7JME0RI2WwfUexTHrodBMitEBTEHM2CKtH6MNVpQXhAs0Olc38MpHrA")


async def main():
    client = await manager.get()
    sess = client.client
    r = await sess.get(URL, headers={"Referer": "https://gemini.google.com/"}, timeout=120)
    data = r.content
    print("status:", r.status_code, flush=True)
    print("content-type:", r.headers.get("content-type"), flush=True)
    print("bytes:", len(data), flush=True)
    print("magic:", data[:16], flush=True)
    is_mp4 = b"ftyp" in data[:64]
    print("IS MP4:", is_mp4, flush=True)
    if is_mp4:
        os.makedirs("media", exist_ok=True)
        with open("media/verified_fox.mp4", "wb") as f:
            f.write(data)
        print("SAVED media/verified_fox.mp4", os.path.getsize("media/verified_fox.mp4"), "bytes", flush=True)
    await manager.reset()


asyncio.run(main())
