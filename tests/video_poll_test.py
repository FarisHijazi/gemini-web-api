"""Unit tests for the video poller's stop/quota handling.

These exist because the poller used to match a hand-written list of English
phrases ("come back tomorrow", ...) to notice an exhausted quota. Google
reworded it to "You're out of videos for now", so the poller matched nothing,
span for the full 600s and then reported a *guess* as the cause. It now keys on
the library's own interrupted/stopped detection, which carries the verbatim
server reason.

A live happy-path run needs unused daily video quota, so the ordering rule that
protects it -- a URL already in hand always wins over a stop signal -- is
covered here instead.
"""
import asyncio

import pytest

from gemini_openai import video

URL = ("https://contribution.usercontent.google.com/download?c=abc"
       "&filename=video.mp4&opi=1")


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeClient:
    """Minimal stand-in: every read_chat drives one _batch_execute round."""

    def __init__(self, bodies, on_read=None):
        self._bodies = list(bodies)
        self._on_read = on_read

    async def _batch_execute(self, payloads, *a, **k):
        body = self._bodies.pop(0) if self._bodies else ""
        return _FakeResponse(body)

    async def read_chat(self, cid, limit=3):
        r = await self._batch_execute(None)
        if self._on_read:
            self._on_read()
        return r


def _stop_log(reason, cid="c_1"):
    """Emit exactly the warning gemini_webapi logs when a turn is stopped."""
    from gemini_webapi.utils.logger import logger

    def _emit():
        # The library formats the cid with `!r`; match that exactly.
        logger.warning(
            f"[read_chat] Gemini generation was interrupted/stopped for {cid!r}. Reason: {reason}"
        )

    return _emit


def test_returns_url_when_video_finishes():
    client = _FakeClient([f'"{URL}"'])
    got = asyncio.run(video._poll_video_url(client, "c_1", timeout=5, interval=0))
    assert got == URL


def test_raises_the_servers_verbatim_reason_not_a_guess():
    reason = "You're out of videos for now. Videos will be available again tomorrow."
    client = _FakeClient(["", ""], on_read=_stop_log(reason))
    with pytest.raises(RuntimeError) as e:
        asyncio.run(video._poll_video_url(client, "c_1", timeout=5, interval=0))
    # The wording Google actually uses today -- which the old phrase list missed.
    assert "out of videos" in str(e.value)


def test_a_finished_url_wins_over_a_stop_signal():
    # Both arrive in the same round: the video is in hand, so the stop is moot.
    client = _FakeClient([f'"{URL}"'], on_read=_stop_log("stopped for some reason"))
    got = asyncio.run(video._poll_video_url(client, "c_1", timeout=5, interval=0))
    assert got == URL


def test_still_generating_is_not_mistaken_for_a_stop():
    # "still working"/"finalized" both appear DURING a healthy generation --
    # observed 4 finalized + 5 still-working lines inside one successful run --
    # so neither may end the poll. Only a URL or a stop signal may.
    from gemini_webapi.utils.logger import logger

    def _noise():
        logger.debug("[read_chat] Gemini is still working on the response for 'c_1'.")
        logger.debug("[read_chat] Gemini has successfully finalized the response for 'c_1'.")

    client = _FakeClient(["", "", f'"{URL}"'], on_read=_noise)
    got = asyncio.run(video._poll_video_url(client, "c_1", timeout=5, interval=0))
    assert got == URL


def test_the_log_sink_is_removed_even_on_failure():
    from gemini_webapi.utils.logger import logger

    before = len(logger._core.handlers)
    client = _FakeClient([""], on_read=_stop_log("nope"))
    with pytest.raises(RuntimeError):
        asyncio.run(video._poll_video_url(client, "c_1", timeout=5, interval=0))
    assert len(logger._core.handlers) == before


def test_another_conversations_stop_does_not_fail_this_poll():
    # Jobs run concurrently and the log sink is process-wide, so a stop in some
    # OTHER conversation must not end this one -- it used to fail every video in
    # flight with an unrelated turn's reason.
    client = _FakeClient(["", f'"{URL}"'], on_read=_stop_log("out of videos", cid="c_other"))
    got = asyncio.run(video._poll_video_url(client, "c_1", timeout=5, interval=0))
    assert got == URL
