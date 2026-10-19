"""Image-to-video reference frames can arrive inline as data URLs from remote clients."""
import base64

import pytest
from fastapi import HTTPException

from gemini_openai import server

PNG = b"\x89PNG\r\n\x1a\nfake"


def test_data_url_becomes_bytes():
    url = "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert server._reference_image(url) == PNG


def test_local_paths_pass_through_unchanged():
    assert server._reference_image("/tmp/frame.png") == "/tmp/frame.png"


@pytest.mark.parametrize("bad", ["data:text/plain;base64,aGk=", "data:image/png,notbase64", "data:image/png;base64,***"])
def test_rejects_non_image_or_invalid_data_urls(bad):
    with pytest.raises(HTTPException) as e:
        server._reference_image(bad)
    assert e.value.status_code == 400
