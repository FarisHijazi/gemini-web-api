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
