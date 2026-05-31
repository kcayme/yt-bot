import queue
import re

from neonize import NewClient
from neonize.events import MessageEv

from . import config
from .downloader import fetch_title
from .logger import get_logger
from .job import Job

logger = get_logger(__name__)

_YOUTUBE_RE = re.compile(
    r"https?://(?:www\.)?(?:"
    r"youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/)"
    r"|youtu\.be/"
    r")[\w-]+"
)

_SHARE_GOOGLE_RE = re.compile(r"https?://share\.google/\S+")

_TRIGGER_RE = re.compile(r"(?:^|\s)@ytbot\b", re.IGNORECASE)


def on_message(
    client: NewClient, message: MessageEv, job_queue: queue.Queue[Job]
) -> None:
    if message.Info.MessageSource.IsGroup:
        return

    src = message.Info.MessageSource
    if not src.IsFromMe and src.Sender.User not in config.ALLOWED_SENDERS:
        logger.info("Rejected sender: %s", src.Sender.User)
        return

    ext = message.Message.extendedTextMessage
    text = message.Message.conversation or ext.text or ""

    if not _TRIGGER_RE.search(text):
        return

    quoted = ext.contextInfo.quotedMessage
    quoted_text = quoted.conversation or quoted.extendedTextMessage.text or ""

    logger.debug("text=%r quoted=%r", text, quoted_text)

    combined = f"{text}\n{quoted_text}"
    urls: list[str] = _YOUTUBE_RE.findall(combined) + _SHARE_GOOGLE_RE.findall(combined)
    if not urls:
        return

    chat = message.Info.MessageSource.Chat

    logger.info("Request from: %s %s", chat.User, chat.USER_FIELD_NUMBER)

    if job_queue.full():
        client.reply_message(
            "I'm currently busy. Please try again once the current batch finishes.",
            message,
        )

        logger.warning("Queue full — rejected %d URL(s)", len(urls))

        return

    titles = [fetch_title(u) for u in urls]

    job_queue.put((chat, urls, titles, message))

    logger.info("Queued %d URL(s)", len(urls))

    lines = [f"{i}. {t}" if t else f"{i}. (unknown)" for i, t in enumerate(titles, 1)]

    client.reply_message(
        "Queued — downloading:\n" + "\n".join(lines),
        message,
    )
