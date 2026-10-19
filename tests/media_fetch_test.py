"""Unit tests for fetching generated media bytes through a connected tab.

The cookie backend generates images and video, but Google 403s any server-side
download of them, so a connected extension tab fetches the bytes. Without a tab
the client still gets the raw (browser-only) URL, as before.
"""
import asyncio
from types import SimpleNamespace

from gemini_openai import config
from gemini_openai import server, video
from gemini_openai.chrome_backend import TabConn, hub

REQ = SimpleNamespace(base_url="http://127.0.0.1:8100/")
URL = "https://lh3.googleusercontent.com/gg-dl/abc"


def _with_tab(monkeypatch, tmp_path, fetch):
    monkeypatch.setattr(video, "MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr(server.chrome_manager, "fetch_bytes", fetch)
    monkeypatch.setitem(hub.conns, 999, TabConn(ws=object(), key=999, tab_id="t"))


def test_no_tab_keeps_raw_url(monkeypatch):
    monkeypatch.setattr(hub, "conns", {})
    assert asyncio.run(server._local_url(URL, REQ)) == URL


def test_tab_fetches_full_size_and_serves_it_locally(monkeypatch, tmp_path):
    asked = []

    async def fetch(url, authuser=None):
        asked.append(url)
        return b"\x89PNG\r\n\x1a\nrest"

    _with_tab(monkeypatch, tmp_path, fetch)
    out = asyncio.run(server._local_url(URL, REQ))
    # Full size, and the account selector -- lh3 403s another account's image.
    assert asked == [URL + "=s2048-rj?authuser=0"]
    assert out.startswith("http://127.0.0.1:8100/files/") and out.endswith(".png")
    assert (tmp_path / out.rsplit("/", 1)[1]).read_bytes().startswith(b"\x89PNG")


def test_failed_tab_fetch_falls_back_to_raw_url(monkeypatch, tmp_path):
    async def fetch(url, authuser=None):
        raise RuntimeError("Failed to fetch")

    _with_tab(monkeypatch, tmp_path, fetch)
    assert asyncio.run(server._local_url(URL, REQ)) == URL


def test_video_download_goes_through_the_tab(monkeypatch, tmp_path):
    async def fetch(url, authuser=None):
        return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32

    _with_tab(monkeypatch, tmp_path, fetch)
    dest = tmp_path / "v.mp4"
    n = asyncio.run(video.download_video(None, "https://x.usercontent.google.com/d", str(dest)))
    assert n == dest.stat().st_size and b"ftyp" in dest.read_bytes()[:64]


def test_failover_video_downloads_as_the_account_that_made_it(monkeypatch, tmp_path):
    # A job that fell over from u/0 to u/1 must fetch with authuser=1 even though
    # the shared client was switched back to the default profile (that was the 403).
    seen = []

    async def fetch(url, authuser=None):
        seen.append((url, authuser))
        return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32

    _with_tab(monkeypatch, tmp_path, fetch)
    monkeypatch.setattr(config, "AUTHUSER", "0")
    dest = tmp_path / "v.mp4"
    asyncio.run(video.download_video(None, "https://x.usercontent.google.com/d?c=1", str(dest), authuser="1"))
    assert seen == [("https://x.usercontent.google.com/d?c=1&authuser=1", "1")]
