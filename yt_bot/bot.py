import argparse
import os
import queue
import signal
import threading
import time
from pathlib import Path

from neonize import NewClient
from neonize.events import ConnectedEv, MessageEv

from . import config
from .handler import on_message
from .logger import get_logger
from .worker import ShutdownContext, run_worker

_SHUTDOWN_GRACE_SECONDS = 10

logger = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rescan", action="store_true", help="Delete saved session and re-scan QR code"
    )
    args = parser.parse_args()

    if args.rescan:
        Path(config.SESSION_DB).unlink(missing_ok=True)
        logger.info("Session cleared — will prompt for QR scan")

    # cleanup temp files
    temp = Path(config.TEMP_DIR)
    if temp.exists():
        for f in temp.iterdir():
            f.unlink(missing_ok=True)
        logger.info("Cleaned up temp dir")

    temp.mkdir(exist_ok=True)

    # create in-memory queue
    job_queue: queue.Queue = queue.Queue(maxsize=config.MAX_QUEUE_SIZE)
    # instantiate whatsapp
    client = NewClient(config.SESSION_DB)

    @client.event(ConnectedEv)
    def on_connected(_: NewClient, __: ConnectedEv) -> None:
        logger.info("Connected to WhatsApp")

    @client.event(MessageEv)
    def message_handler(c: NewClient, message: MessageEv) -> None:
        on_message(c, message, job_queue)

    ctx = ShutdownContext()
    threading.Thread(
        target=run_worker, args=(client, job_queue, ctx), daemon=True
    ).start()

    # client.connect() blocks inside the Go runtime and ignores KeyboardInterrupt.
    # Run it in a daemon thread so the main thread stays free to handle signals.
    stop = threading.Event()

    def handle_signal(*_):
        if stop.is_set():
            # Second Ctrl+C — bypass grace period, exit immediately.
            logger.warning("Force exit")
            os._exit(130)
        logger.info("Shutdown requested — cancelling active downloads")
        ctx.cancel_all()
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    threading.Thread(target=client.connect, daemon=True).start()

    logger.info("Starting bot — scan the QR code if prompted")
    stop.wait()

    # Give in-flight downloads a chance to abort cleanly via their cancel events.
    deadline = time.monotonic() + _SHUTDOWN_GRACE_SECONDS
    while ctx.has_active() and time.monotonic() < deadline:
        time.sleep(0.2)

    if ctx.has_active():
        logger.warning(
            "Grace period elapsed with active jobs — forcing exit"
        )
    else:
        logger.info("All jobs aborted cleanly")

    # ThreadPoolExecutor's atexit hook would otherwise wait for any remaining
    # work, and yt-dlp's C-level network code ignores signals.
    os._exit(0)


if __name__ == "__main__":
    main()
