import pytest

from yt_bot.handler import _SHARE_GOOGLE_RE, _TRIGGER_RE, _YOUTUBE_RE


@pytest.mark.parametrize(
    "text,expected",
    [
        ("@ytbot https://youtu.be/x", True),
        ("hey @ytbot grab this", True),
        ("@YTBOT", True),
        ("@Ytbot please", True),
        ("  @ytbot", True),
        ("line1\n@ytbot here", True),
        ("email@ytbot.com", False),
        ("foo@ytbot", False),
        ("@ytbotx", False),
        ("ytbot", False),
        ("", False),
        ("just a youtube link https://youtu.be/x", False),
    ],
)
def test_trigger_regex(text, expected):
    assert bool(_TRIGGER_RE.search(text)) is expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://youtu.be/abc123", ["https://youtu.be/abc123"]),
        ("https://www.youtu.be/abc123", ["https://www.youtu.be/abc123"]),
        (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        ),
        (
            "https://youtube.com/watch?v=abc_DEF-123",
            ["https://youtube.com/watch?v=abc_DEF-123"],
        ),
        (
            "https://youtube.com/shorts/xyz123",
            ["https://youtube.com/shorts/xyz123"],
        ),
        (
            "https://youtube.com/embed/xyz123",
            ["https://youtube.com/embed/xyz123"],
        ),
        (
            "https://youtube.com/watch?feature=share&v=abc123",
            ["https://youtube.com/watch?feature=share&v=abc123"],
        ),
        ("https://example.com/video", []),
        ("https://vimeo.com/12345", []),
        ("", []),
        (
            "first https://youtu.be/aaa and https://youtu.be/bbb",
            ["https://youtu.be/aaa", "https://youtu.be/bbb"],
        ),
    ],
)
def test_youtube_regex(text, expected):
    assert _YOUTUBE_RE.findall(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://share.google/abc", ["https://share.google/abc"]),
        ("text https://share.google/xY_z-12 trail", ["https://share.google/xY_z-12"]),
        ("https://share.google/", []),
        ("https://google.com/search", []),
        ("", []),
    ],
)
def test_share_google_regex(text, expected):
    assert _SHARE_GOOGLE_RE.findall(text) == expected
