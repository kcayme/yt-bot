import queue
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import TYPE_CHECKING, Any

from neonize import NewClient
from neonize.events import MessageEv

from . import config
from .downloader import download_all, infer_mimetype
from .logger import get_logger
from .utils import new_id_hex

if TYPE_CHECKING:
    from .worker import ShutdownContext

logger = get_logger(__name__)

# (chat JID, URLs to download, pre-fetched titles (None if unknown), original message)
Job = tuple[Any, list[str], list[str | None], MessageEv]


def run_job(
    client: NewClient,
    chat,
    urls: list[str],
    titles: list[str | None],
    original_message,
    job_queue: "queue.Queue[Job]",
    ctx: "ShutdownContext",
) -> None:
    cancel = threading.Event()
    ctx.register(cancel)

    executor = ThreadPoolExecutor(max_workers=1)
    progress: dict[str, str] = {
        url: title or url for url, title in zip(urls, titles)
    }

    try:
        if ctx.shutdown.is_set():
            logger.info("Skipping queued job — shutdown in progress")
            return

        logger.info("Processing job: %d URL(s)", len(urls))

        future = executor.submit(
            process_job, client, chat, urls, original_message, cancel, progress
        )
        future.result(timeout=config.JOB_TIMEOUT)

    except FuturesTimeoutError:
        cancel.set()
        mins = config.JOB_TIMEOUT // 60
        pending = list(progress.values())

        logger.error(
            "Job timed out after %d minutes (pending: %s)", mins, pending
        )

        if not ctx.shutdown.is_set():
            suffix = (
                f" while processing:\n" + "\n".join(f"• {p}" for p in pending)
                if pending
                else ""
            )
            client.reply_message(
                f"Download timed out after {mins} minutes{suffix}\n\nPlease try again.",
                original_message,
            )

    except Exception:
        logger.exception("Unhandled error processing job")

    finally:
        ctx.unregister(cancel)
        executor.shutdown(wait=False)
        job_queue.task_done()


def process_job(
    client: NewClient,
    chat,
    urls: list[str],
    original_message,
    cancel: threading.Event,
    progress: dict[str, str],
) -> None:
    total = len(urls)
    errors: list[str] = []
    count = 0
    job_dir = Path(config.TEMP_DIR) / new_id_hex()
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        for url, result, error in download_all(urls, cancel, job_dir):
            count += 1

            if result and "title" in result:
                progress[url] = result["title"]

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
                    progress.pop(url, None)

            else:
                errors.append(f"• {url}: {error}")
                progress.pop(url, None)

        if errors and not cancel.is_set():
            client.reply_message(
                "Failed downloads:\n" + "\n".join(errors),
                original_message,
            )

    finally:
        # Wipe the per-job dir — catches partials, orphan writes, and any
        # downloaded-but-unsent files left after a cancel.
        shutil.rmtree(job_dir, ignore_errors=True)
