import io
import json
import threading
from unittest.mock import MagicMock, patch

import pytest
import yt_dlp

from yt_bot import config, downloader


@pytest.mark.parametrize(
    "err,expected",
    [
        ("ERROR: Video unavailable", "video is private or unavailable"),
        ("This video is private", "video is private or unavailable"),
        ("Video has been removed", "video is private or unavailable"),
        ("Not available in your country", "not available in your region"),
        ("Geo-restricted content", "not available in your region"),
        ("ERROR: Unsupported URL: foo", "not a valid YouTube URL"),
        ("Not a valid URL", "not a valid YouTube URL"),
        ("", "unknown error"),
        ("   ", "unknown error"),
        ("first line\nsecond line\nthird line", "third line"),
    ],
)
def test_friendly_error(err, expected):
    assert downloader._friendly_error(err) == expected


def test_friendly_error_truncates_long_lines():
    long = "x" * 500
    assert len(downloader._friendly_error(long)) == 150


@pytest.mark.parametrize(
    "filepath,expected_prefix",
    [
        ("video.mp4", "video/"),
        ("audio.mp3", "audio/"),
        ("clip.webm", "video/"),
    ],
)
def test_infer_mimetype_known(filepath, expected_prefix):
    assert downloader.infer_mimetype(filepath).startswith(expected_prefix)


def test_infer_mimetype_unknown_defaults_to_mp4():
    assert downloader.infer_mimetype("file.weirdext") == "video/mp4"


def _fake_response(url: str, body: bytes = b""):
    resp = MagicMock()
    resp.url = url
    resp.read.return_value = body
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, *a: False
    return resp


def test_resolve_url_simple_redirect():
    final_url = "https://youtu.be/abc123"
    with patch.object(downloader.urllib.request, "urlopen", return_value=_fake_response(final_url)):
        assert downloader._resolve_url("https://share.google/xyz") == final_url


def test_resolve_url_extracts_from_google_search():
    search_url = "https://www.google.com/search?q=foo"
    html = b'<html>... https://www.youtube.com/watch?v=abc123 ...</html>'

    responses = [_fake_response(search_url), _fake_response(search_url, body=html)]
    with patch.object(downloader.urllib.request, "urlopen", side_effect=responses):
        result = downloader._resolve_url("https://share.google/xyz")
    assert result == "https://www.youtube.com/watch?v=abc123"


def test_resolve_url_google_search_no_match_returns_search_url():
    search_url = "https://www.google.com/search?q=foo"
    html = b"<html>no video here</html>"
    responses = [_fake_response(search_url), _fake_response(search_url, body=html)]
    with patch.object(downloader.urllib.request, "urlopen", side_effect=responses):
        result = downloader._resolve_url("https://share.google/xyz")
    assert result == search_url


def test_fetch_title_success():
    body = json.dumps({"title": "My Video"}).encode()
    resp = _fake_response("ignored", body=body)
    with patch.object(downloader.urllib.request, "urlopen", return_value=resp):
        assert downloader.fetch_title("https://youtu.be/abc") == "My Video"


def test_fetch_title_failure_returns_none():
    with patch.object(downloader.urllib.request, "urlopen", side_effect=Exception("boom")):
        assert downloader.fetch_title("https://youtu.be/abc") is None


def test_fetch_title_resolves_share_google_first():
    resolved = "https://youtu.be/real"
    body = json.dumps({"title": "Resolved"}).encode()

    with patch.object(downloader, "_resolve_url", return_value=resolved) as mock_resolve, \
         patch.object(downloader.urllib.request, "urlopen", return_value=_fake_response("x", body=body)):
        title = downloader.fetch_title("https://share.google/xyz")

    mock_resolve.assert_called_once_with("https://share.google/xyz")
    assert title == "Resolved"


def test_fetch_title_resolve_failure_returns_none():
    with patch.object(downloader, "_resolve_url", side_effect=Exception("nope")):
        assert downloader.fetch_title("https://share.google/xyz") is None


@pytest.mark.parametrize(
    "err,expected",
    [
        ("Video is private", True),
        ("This video is unavailable", True),
        ("Video has been removed", True),
        ("Geo-restricted content", True),
        ("Unsupported URL: foo", True),
        ("HTTP Error 503: Service Unavailable", True),  # "unavailable" matches
        ("Connection reset by peer", False),
        ("HTTP Error 429: Too Many Requests", False),
        ("Read timed out", False),
        ("", False),
    ],
)
def test_is_permanent_error(err, expected):
    assert downloader._is_permanent_error(err) is expected


@pytest.fixture
def fast_retry(monkeypatch):
    monkeypatch.setattr(config, "DOWNLOAD_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(config, "DOWNLOAD_RETRY_BACKOFFS", [0, 0])


def test_download_sync_succeeds_first_try(fast_retry, tmp_path):
    cancel = threading.Event()
    expected = {"filepath": "/tmp/x.mp4", "title": "T"}
    with patch.object(downloader, "_run_attempt_with_timeout", return_value=expected) as mock:
        assert downloader._download_sync("https://youtu.be/abc", cancel, tmp_path) == expected
    mock.assert_called_once()


def test_download_sync_retries_transient_then_succeeds(fast_retry, tmp_path):
    cancel = threading.Event()
    transient = yt_dlp.utils.DownloadError("HTTP Error 429: Too Many Requests")
    expected = {"filepath": "/tmp/x.mp4", "title": "T"}
    with patch.object(
        downloader, "_run_attempt_with_timeout", side_effect=[transient, transient, expected]
    ) as mock:
        assert downloader._download_sync("https://youtu.be/abc", cancel, tmp_path) == expected
    assert mock.call_count == 3


def test_download_sync_no_retry_on_permanent_error(fast_retry, tmp_path):
    cancel = threading.Event()
    perm = yt_dlp.utils.DownloadError("Video is private")
    with patch.object(downloader, "_run_attempt_with_timeout", side_effect=perm) as mock:
        with pytest.raises(yt_dlp.utils.DownloadError):
            downloader._download_sync("https://youtu.be/abc", cancel, tmp_path)
    mock.assert_called_once()


def test_download_sync_gives_up_after_max_attempts(fast_retry, tmp_path):
    cancel = threading.Event()
    transient = yt_dlp.utils.DownloadError("Connection reset by peer")
    with patch.object(downloader, "_run_attempt_with_timeout", side_effect=transient) as mock:
        with pytest.raises(yt_dlp.utils.DownloadError):
            downloader._download_sync("https://youtu.be/abc", cancel, tmp_path)
    assert mock.call_count == 3


def test_download_sync_does_not_retry_when_cancelled(fast_retry, tmp_path):
    cancel = threading.Event()
    cancel.set()
    with patch.object(downloader, "_run_attempt_with_timeout") as mock:
        with pytest.raises(downloader.DownloadCancelled):
            downloader._download_sync("https://youtu.be/abc", cancel, tmp_path)
    mock.assert_not_called()


def test_download_sync_propagates_cancel_during_attempt(fast_retry, tmp_path):
    cancel = threading.Event()
    with patch.object(
        downloader,
        "_run_attempt_with_timeout",
        side_effect=downloader.DownloadCancelled("timeout"),
    ) as mock:
        with pytest.raises(downloader.DownloadCancelled):
            downloader._download_sync("https://youtu.be/abc", cancel, tmp_path)
    mock.assert_called_once()


def test_download_sync_resolves_share_google_before_retry_loop(fast_retry, tmp_path):
    cancel = threading.Event()
    expected = {"filepath": "/tmp/x.mp4", "title": "T"}
    with patch.object(downloader, "_resolve_url", return_value="https://youtu.be/real") as mock_resolve, \
         patch.object(downloader, "_run_attempt_with_timeout", return_value=expected) as mock_dl:
        downloader._download_sync("https://share.google/xyz", cancel, tmp_path)
    mock_resolve.assert_called_once_with("https://share.google/xyz")
    mock_dl.assert_called_once()
    assert mock_dl.call_args[0][0] == "https://youtu.be/real"


def test_download_sync_retries_on_attempt_timeout(fast_retry, tmp_path):
    cancel = threading.Event()
    expected = {"filepath": "/tmp/x.mp4", "title": "T"}
    with patch.object(
        downloader,
        "_run_attempt_with_timeout",
        side_effect=[downloader.FuturesTimeoutError(), expected],
    ) as mock:
        assert downloader._download_sync("https://youtu.be/abc", cancel, tmp_path) == expected
    assert mock.call_count == 2


def test_download_sync_attempt_timeout_yields_to_parent_cancel(fast_retry, tmp_path):
    cancel = threading.Event()

    def side_effect(*_a, **_kw):
        cancel.set()
        raise downloader.FuturesTimeoutError()

    with patch.object(downloader, "_run_attempt_with_timeout", side_effect=side_effect):
        with pytest.raises(downloader.DownloadCancelled):
            downloader._download_sync("https://youtu.be/abc", cancel, tmp_path)


def test_run_attempt_with_timeout_returns_result(tmp_path):
    cancel = threading.Event()
    expected = {"filepath": "/tmp/x.mp4", "title": "T"}
    with patch.object(downloader, "_download_once", return_value=expected) as mock:
        result = downloader._run_attempt_with_timeout("https://x", cancel, timeout=5, output_dir=tmp_path)
    assert result == expected
    assert mock.call_count == 1


def test_run_attempt_with_timeout_propagates_timeout(tmp_path):
    import time as _time
    cancel = threading.Event()

    def slow(_url, _cancel, _out):
        _time.sleep(2)
        return {"filepath": "x", "title": "y"}

    with patch.object(downloader, "_download_once", side_effect=slow):
        with pytest.raises(downloader.FuturesTimeoutError):
            downloader._run_attempt_with_timeout("https://x", cancel, timeout=0.05, output_dir=tmp_path)


def test_any_event_logic():
    a = threading.Event()
    b = threading.Event()
    composite = downloader._AnyEvent(a, b)
    assert not composite.is_set()
    a.set()
    assert composite.is_set()
    a.clear()
    assert not composite.is_set()
    b.set()
    assert composite.is_set()
