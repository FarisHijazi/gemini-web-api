"""Unit tests for the video overlay applied to the library's generate payload.

Portrait was only solved on 2026-10-13: inner[55]=[[17]] alone still returned
1280x720; the UI's tool flag 2 in message_content[9] is what makes 720x1280.
"""
import json

from gemini_openai import video


class Recorder:
    def dumps(self, obj, *args, **kwargs):
        return json.dumps(obj)


def overlay(aspect):
    proxy = video._JsonProxy(Recorder())
    payload = [["prompt"]] + [None] * 80
    t1 = video._video_ctx.set(True)
    t2 = video._video_ctx_aspect.set(aspect)
    try:
        return json.loads(proxy.dumps(payload))
    finally:
        video._video_ctx_aspect.reset(t2)
        video._video_ctx.reset(t1)


def test_portrait_sends_tool_flag_2_and_aspect_17():
    out = overlay(video.ASPECT_PORTRAIT)
    assert out[0][9][6] == [[None, None, None, 2]]
    assert out[55] == [[17]] and out[17] == [[1]]


def test_landscape_keeps_tool_flag_1_and_aspect_16():
    out = overlay(video.ASPECT_LANDSCAPE)
    assert out[0][9][6] == [[None, None, None, 1]]
    assert out[55] == [[16]]


def test_non_video_payload_is_untouched():
    proxy = video._JsonProxy(Recorder())
    payload = [["prompt"]] + [None] * 80
    assert json.loads(proxy.dumps(payload)) == payload
