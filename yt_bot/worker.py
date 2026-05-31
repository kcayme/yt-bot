import queue
import threading
from concurrent.futures import ThreadPoolExecutor

from neonize import NewClient

from . import config
from .job import Job, run_job
from .logger import get_logger

logger = get_logger(__name__)


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
                chat, urls, titles, original_message = job_queue.get(timeout=0.5)
                pool.submit(
                    run_job,
                    client,
                    chat,
                    urls,
                    titles,
                    original_message,
                    job_queue,
                    ctx,
                )
            except queue.Empty:
                continue
            except Exception:
                logger.exception(
                    "Something went wrong ",
                )
                continue
