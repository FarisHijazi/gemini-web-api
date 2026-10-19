"""Unit tests for the video poller's terminal conditions.

The poller reads the finished video from the library's own parsed `read_chat`
output, and learns that Gemini stopped from the library's own stop warning --
it used to scrape raw responses with a regex and match guessed English quota
phrases, which Google reworded past ("You're out of videos for now").

A live happy-path run needs unused daily video quota, so the ordering rules
are covered here with a fake client.
"""
import asyncio
from types import SimpleNamespace

import pytest

from gemini_openai import video

URL = "https://contribution.usercontent.google.com/download?c=abc&filename=video.mp4&opi=1"


def _history(*video_urls):
    """A read_chat result: the newest model turn, carrying these videos."""
    out = SimpleNamespace(videos=[SimpleNamespace(url=u) for u in video_urls])
    return SimpleNamespace(turns=[SimpleNamespace(model_output=out),
                                  SimpleNamespace(model_output=None)])


class _FakeClient:
    """Each read_chat returns the next scripted result (None = still working)."""

    def __init__(self, results, on_read=None):
        self._results = list(results)
        self._on_read = on_read

    async def read_chat(self, cid, limit=2):
        if self._on_read:
            self._on_read()
        return self._results.pop(0) if self._results else None


def _stop_log(reason, cid="c_1"):
    """Emit exactly the warning gemini_webapi logs when a turn is stopped."""
    from gemini_webapi import logger

    def _emit():
        # The library formats the cid with `!r`; match that exactly.
        logger.warning(
            f"[read_chat] Gemini generation was interrupted/stopped for {cid!r}. Reason: {reason}"
        )

    return _emit


GIF = "https://lh3.googleusercontent.com/gg/abc123"


@pytest.fixture(autouse=True)
def _no_animated_image(monkeypatch):
    """Paid-account behaviour by default: the turn carries no free-plan GIF."""
    async def none(client, cid):
        return None
    monkeypatch.setattr(video, "_read_animated_image", none)


def _gif_after(monkeypatch, reads):
    """The free-plan GIF appears on the `reads`-th raw read."""
    seen = []

    async def read(client, cid):
        seen.append(cid)
        return GIF if len(seen) >= reads else None
    monkeypatch.setattr(video, "_read_animated_image", read)


def _poll(client):
    return asyncio.run(video._poll_video_url(client, "c_1", timeout=5, interval=0))


def test_returns_url_when_video_finishes():
    assert _poll(_FakeClient([None, _history(URL)])) == URL


def test_a_finalized_turn_without_a_video_keeps_polling():
    # The text reply finalizes while Veo is still rendering (seen 4x inside one
    # successful run), so a turn with no video yet is not a stop.
    assert _poll(_FakeClient([_history(), _history(), _history(URL)])) == URL


def test_raises_the_servers_verbatim_reason_not_a_guess():
    reason = "You're out of videos for now. Videos will be available again tomorrow."
    with pytest.raises(video.VideoStopped) as e:
        _poll(_FakeClient([_history()], on_read=_stop_log(reason)))
    assert "out of videos" in str(e.value)


def test_a_stop_triggers_profile_failover():
    # An explicit stop must count as exhaustion so the next profile is tried.
    assert video._is_quota_failure(video.VideoStopped("Gemini stopped generating: x"))


def test_a_finished_url_wins_over_a_stop_signal():
    assert _poll(_FakeClient([_history(URL)], on_read=_stop_log("stopped"))) == URL


def test_another_conversations_stop_does_not_fail_this_poll():
    # Jobs run concurrently and the log sink is process-wide, so a stop in some
    # OTHER conversation must not end this one.
    client = _FakeClient([None, _history(URL)], on_read=_stop_log("out of videos", cid="c_other"))
    assert _poll(client) == URL


def test_the_log_sink_is_removed_even_on_failure():
    from gemini_webapi import logger

    before = len(logger._core.handlers)
    with pytest.raises(video.VideoStopped):
        _poll(_FakeClient([None], on_read=_stop_log("nope")))
    assert len(logger._core.handlers) == before


def test_a_free_plan_gif_is_the_result_when_no_video_comes(monkeypatch):
    _gif_after(monkeypatch, 2)
    assert _poll(_FakeClient([_history(), _history(), _history()])) == GIF


def test_a_free_plan_gif_wins_over_the_stop_signal_free_accounts_also_log(monkeypatch):
    _gif_after(monkeypatch, 1)
    assert _poll(_FakeClient([_history()], on_read=_stop_log("free plan"))) == GIF


def test_a_real_video_wins_over_a_gif(monkeypatch):
    _gif_after(monkeypatch, 1)
    assert _poll(_FakeClient([_history(URL)])) == URL


def test_finds_the_gif_attachment_in_nested_json_strings():
    # Shape seen live (2026-10-19): the attachment list sits inside a JSON string
    # inside the RPC envelope, next to unrelated contribution/usercontent entries.
    inner = [[["c_1", "r_1"], None, None, [[["rc_1", ["Because you're on a free plan..."],
             [None, 3, "omni_context.pb", None, None, "x", None,
              ["", "https://contribution.usercontent.google.com/download?c=1", ""]],
             [[None, 1, "animated_image.gif", GIF, None, "token"]]]]]]]
    envelope = [["wrb.fr", "hNvQHb", __import__("json").dumps([inner])]]
    assert video.find_animated_image(envelope) == GIF


def test_ignores_non_gif_and_foreign_hosts():
    assert video.find_animated_image([[None, 1, "photo.png", GIF]]) is None
    assert video.find_animated_image([[None, 1, "x.gif", "https://example.com/x.gif"]]) is None
    assert video.find_animated_image("not json") is None
