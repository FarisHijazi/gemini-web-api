"""Image-to-video reference frames can arrive inline as data URLs from remote clients."""
import base64

import pytest
from fastapi import HTTPException

from gemini_openai import server

PNG = b"\x89PNG\r\n\x1a\nfake"


@pytest.mark.parametrize("mime,ext", [("image/png", ".png"), ("image/jpeg", ".jpg")])
def test_data_url_becomes_a_typed_file(monkeypatch, tmp_path, mime, ext):
    # The upload's MIME type comes from the filename; extensionless bytes upload as
    # application/octet-stream and Gemini ignores them as a starting frame.
    monkeypatch.setattr(server.video_mod, "MEDIA_DIR", str(tmp_path))
    path = server._reference_image(f"data:{mime};base64," + base64.b64encode(PNG).decode())
    assert path.endswith(ext) and path.startswith(str(tmp_path))
    assert open(path, "rb").read() == PNG


def test_local_paths_pass_through_unchanged():
    assert server._reference_image("/tmp/frame.png") == "/tmp/frame.png"


@pytest.mark.parametrize("bad", ["data:text/plain;base64,aGk=", "data:image/png,notbase64", "data:image/png;base64,***"])
def test_rejects_non_image_or_invalid_data_urls(bad):
    with pytest.raises(HTTPException) as e:
        server._reference_image(bad)
    assert e.value.status_code == 400
