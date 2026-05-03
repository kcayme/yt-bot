import queue
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any

from neonize import NewClient
from neonize.events import MessageEv

from . import config
from .downloader import download_all, infer_mimetype
from .logger import get_logger
from .utils import new_id_hex

logger = get_logger(__name__)

# (chat JID, URLs to download, original message for replies/quoting)
Job = tuple[Any, list[str], MessageEv]


class ShutdownContext:
    """Shared state for coordinating graceful shutdown across threads."""

    def __init__(self) -> None:
        self.shutdown = threading.Event()
        self._lock = threading.Lock()
        self._active: set[threading.Event] = set()

    def register(self, cancel: threading.Event) -> None:
        with self._lock:
            self._active.add(cancel)

    def unregister(self, cancel: threading.Event) -> None:
        with self._lock:
            self._active.discard(cancel)

    def cancel_all(self) -> None:
        self.shutdown.set()
        with self._lock:
            for c in self._active:
                c.set()

    def has_active(self) -> bool:
        with self._lock:
            return bool(self._active)


def run_worker(
    client: NewClient,
    job_queue: "queue.Queue[Job]",
    ctx: ShutdownContext,
) -> None:
    """Continuously consumes jobs from the queue and processes them."""

    with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_JOBS) as pool:
        while not ctx.shutdown.is_set():
            try:
                chat, urls, original_message = job_queue.get(timeout=0.5)
                pool.submit(
                    _run_job, client, chat, urls, original_message, job_queue, ctx
                )
            except queue.Empty:
                continue
            except Exception:
                logger.exception(
                    "Something went wrong ",
                )
                continue


def _run_job(
    client: NewClient,
    chat,
    urls: list[str],
    original_message,
    job_queue: "queue.Queue[Job]",
    ctx: ShutdownContext,
) -> None:
    cancel = threading.Event()
    ctx.register(cancel)

    executor = ThreadPoolExecutor(max_workers=1)

    try:
        if ctx.shutdown.is_set():
            logger.info("Skipping queued job — shutdown in progress")
            return

        logger.info("Processing job: %d URL(s)", len(urls))

        future = executor.submit(
            _process_job, client, chat, urls, original_message, cancel
        )
        future.result(timeout=config.JOB_TIMEOUT_MS)

    except FuturesTimeoutError:
        cancel.set()
        mins = config.JOB_TIMEOUT_MS // 60

        logger.error("Job timed out after %d minutes", mins)

        if not ctx.shutdown.is_set():
            client.reply_message(
                f"Download timed out after {mins} minutes. Please try again.",
                original_message,
            )

    except Exception:
        logger.exception("Unhandled error processing job")

    finally:
        ctx.unregister(cancel)
        executor.shutdown(wait=False)
        job_queue.task_done()


def _process_job(
    client: NewClient,
    chat,
    urls: list[str],
    original_message,
    cancel: threading.Event,
) -> None:
    total = len(urls)
    errors: list[str] = []
    count = 0
    job_dir = Path(config.TEMP_DIR) / new_id_hex()
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        for url, result, error in download_all(urls, cancel, job_dir):
            count += 1

            if cancel.is_set():
                logger.info("Aborting send loop due to cancel")

                break

            if result:
                filepath = result["filepath"]
                title = result["title"]
                ext = Path(filepath).suffix or ".mp4"
                filename = f"{title}{ext}"
                mimetype = infer_mimetype(filepath)

                try:
                    logger.info("Sending [%d/%d]: %s", count, total, title)

                    client.send_document(
                        chat,
                        filepath,
                        filename=filename,
                        mimetype=mimetype,
                        quoted=original_message,
                    )

                    logger.info("Sent [%d/%d]: %s", count, total, title)

                except Exception as e:
                    logger.error(
                        "Failed to send [%d/%d] %s: %s", count, total, title, e
                    )

                    errors.append(f"• {url}: failed to send file ({e})")

                finally:
                    Path(filepath).unlink(missing_ok=True)

            else:
                errors.append(f"• {url}: {error}")

        if errors and not cancel.is_set():
            client.reply_message(
                "Failed downloads:\n" + "\n".join(errors),
                original_message,
            )

    finally:
        # Wipe the per-job dir — catches partials, orphan writes, and any
        # downloaded-but-unsent files left after a cancel.
        shutil.rmtree(job_dir, ignore_errors=True)
