from gemini_openai.video import with_authuser


def test_appends_authuser_to_query_url():
    assert with_authuser("https://h/download?c=x&filename=video.mp4", "1") == \
        "https://h/download?c=x&filename=video.mp4&authuser=1"


def test_url_without_query_gets_question_mark():
    assert with_authuser("https://h/v.mp4", "2") == "https://h/v.mp4?authuser=2"


def test_existing_authuser_is_kept():
    assert with_authuser("https://h/d?c=x&authuser=3", "1") == "https://h/d?c=x&authuser=3"


def test_image_candidates_ignore_the_video_quota_cache(monkeypatch):
    from gemini_openai import config, video

    monkeypatch.setattr(config, "AUTHUSER", "0")
    monkeypatch.setenv("GEMINI_AUTHUSER_FALLBACKS", "0,1,2")
    monkeypatch.setattr(video, "_quota_cache_read", lambda: {"0": 1e18, "1": 1e18})
    assert video._profile_candidates() == ["2"]
    assert video._profile_candidates(use_quota_cache=False) == ["0", "1", "2"]
