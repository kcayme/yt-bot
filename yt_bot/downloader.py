import json
import mimetypes
import re
import threading
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path

import yt_dlp

from . import config
from .logger import get_logger

logger = get_logger(__name__)


_YT_URL_RE = re.compile(r"https?://(?:www\.)?youtube\.com/watch\?v=[\w-]+")


def _resolve_url(url: str) -> str:
    """Follow redirects and return the final URL.

    If the redirect lands on a Google search results page, extract the
    embedded YouTube URL from the HTML instead.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        final = resp.url

    if "google.com/search" in final:
        req = urllib.request.Request(final, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        match = _YT_URL_RE.search(html)
        if match:
            return match.group(0)

    return final


def fetch_title(url: str) -> str | None:
    """Return the video title via YouTube oEmbed, or None on failure."""
    if "share.google" in url:
        try:
            url = _resolve_url(url)
        except Exception as e:
            logger.warning("Failed to resolve share.google URL %s: %s", url, e)
            return None
    oembed_url = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe="")
    req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())["title"]
    except Exception as e:
        logger.warning("Failed to fetch title for %s: %s", url, e)


class DownloadCancelled(Exception):
    pass


class _AnyEvent:
    """Lightweight composite — `is_set()` is True if any wrapped event is set."""

    def __init__(self, *events: threading.Event) -> None:
        self._events = events

    def is_set(self) -> bool:
        return any(e.is_set() for e in self._events)


def _is_permanent_error(error: str) -> bool:
    msg = error.lower()
    return any(
        kw in msg
        for kw in (
            "private",
            "unavailable",
            "removed",
            "geo",
            "not available in your country",
            "unsupported url",
            "not a valid url",
        )
    )


def _download_once(url: str, cancel, output_dir: Path) -> dict:
    state = {"started": False}

    def progress_hook(d: dict) -> None:
        if cancel.is_set():
            raise DownloadCancelled("Download cancelled")
        status = d.get("status")
        if status == "downloading" and not state["started"]:
            state["started"] = True
            logger.info("Started download: %s", url)
        elif status == "finished":
            logger.info("Finished download: %s", url)

    uid = uuid.uuid7().hex
    output_template = str(output_dir / f"{uid}.%(ext)s")
    opts = {
        "format": config.DOWNLOAD_FORMAT,
        "format_sort": config.FORMAT_SORT,
        "outtmpl": output_template,
        "concurrent_fragment_downloads": config.CONCURRENT_FRAGMENTS,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [progress_hook],
        "socket_timeout": 30,
        "retries": 10,
        "fragment_retries": 10,
        "retry_sleep_functions": {
            "http": lambda n: min(2 ** n, 30),
            "fragment": lambda n: min(2 ** n, 30),
        },
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if "requested_downloads" in info:
            filepath = info["requested_downloads"][0]["filepath"]
        else:
            filepath = ydl.prepare_filename(info)
        return {"filepath": filepath, "title": info.get("title", "video")}


def _run_attempt_with_timeout(
    url: str, cancel: threading.Event, timeout: float, output_dir: Path
) -> dict:
    """Run one _download_once with a per-attempt wall-clock timeout.

    On timeout, sets a per-attempt cancel that the progress hook honors so the
    orphaned worker exits at its next chunk. The parent `cancel` is also
    watched, so a job-level/shutdown cancel still aborts immediately.
    """
    attempt_cancel = threading.Event()
    composite = _AnyEvent(cancel, attempt_cancel)
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_download_once, url, composite, output_dir)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            attempt_cancel.set()
            raise
    finally:
        # wait=False: the orphaned thread will unwind on its next progress tick.
        executor.shutdown(wait=False)


def _download_sync(url: str, cancel: threading.Event, output_dir: Path) -> dict:
    """Blocking yt-dlp download with per-attempt timeout and transient retries."""
    logger.info("Queued for download: %s", url)
    if "share.google" in url:
        url = _resolve_url(url)

    max_attempts = max(1, config.DOWNLOAD_MAX_ATTEMPTS)
    backoffs = config.DOWNLOAD_RETRY_BACKOFFS
    attempt_timeout = config.DOWNLOAD_ATTEMPT_TIMEOUT

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        if cancel.is_set():
            raise DownloadCancelled("Download cancelled")
        try:
            return _run_attempt_with_timeout(url, cancel, attempt_timeout, output_dir)
        except DownloadCancelled:
            raise
        except FuturesTimeoutError:
            # Per-attempt timeout — parent cancel takes priority.
            if cancel.is_set():
                raise DownloadCancelled("Download cancelled")
            last_exc = TimeoutError(
                f"attempt timed out after {attempt_timeout}s"
            )
            transient = True
        except yt_dlp.utils.DownloadError as e:
            last_exc = e
            transient = not _is_permanent_error(str(e))
        except Exception as e:
            last_exc = e
            transient = True
        else:
            continue  # unreachable (return on success), keeps mypy happy

        if not transient or attempt >= max_attempts:
            raise last_exc

        wait = backoffs[min(attempt - 1, len(backoffs) - 1)]
        logger.warning(
            "Download attempt %d/%d failed for %s: %s — retrying in %ds",
            attempt,
            max_attempts,
            url,
            last_exc,
            wait,
        )
        if cancel.wait(wait):
            raise DownloadCancelled("Download cancelled during retry backoff")

    assert last_exc is not None
    raise last_exc


def download_all(urls: list[str], cancel: threading.Event, output_dir: Path):
    """Yield (url, result, error) tuples in completion order.

    Downloads run in parallel; the caller processes each result (e.g. uploads it)
    as soon as that download finishes, while the others continue downloading.
    """
    with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_DOWNLOADS) as executor:
        futures = {
            executor.submit(_download_sync, url, cancel, output_dir): url
            for url in urls
        }

        for future in as_completed(futures):
            url = futures[future]

            try:
                yield url, future.result(), None
            except DownloadCancelled:
                yield url, None, "cancelled due to timeout"
            except yt_dlp.utils.DownloadError as e:
                yield url, None, _friendly_error(str(e))
            except Exception as e:
                yield url, None, str(e)[:150]


def _friendly_error(error: str) -> str:
    msg = error.lower()
    if "private" in msg or "unavailable" in msg or "removed" in msg:
        return "video is private or unavailable"
    if "geo" in msg or "not available in your country" in msg:
        return "not available in your region"
    if "unsupported url" in msg or "not a valid url" in msg:
        return "not a valid YouTube URL"
    lines = [l.strip() for l in error.strip().splitlines() if l.strip()]
    return lines[-1][:150] if lines else "unknown error"


def infer_mimetype(filepath: str) -> str:
    mime, _ = mimetypes.guess_type(filepath)
    return mime or "video/mp4"
