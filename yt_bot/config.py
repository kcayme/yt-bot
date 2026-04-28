import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw is not None and raw != "" else default


def _str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return raw if raw is not None and raw != "" else default


def _csv_int(name: str, default: list[int]) -> list[int]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return [int(p.strip()) for p in raw.split(",") if p.strip()]


def _csv_str(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return [p.strip() for p in raw.split(",") if p.strip()]


def _csv_set(name: str, default: set[str]) -> set[str]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return {p.strip() for p in raw.split(",") if p.strip()}


MAX_QUEUE_SIZE = _int("MAX_QUEUE_SIZE", 3)
MAX_CONCURRENT_JOBS = _int("MAX_CONCURRENT_JOBS", 2)
MAX_CONCURRENT_DOWNLOADS = _int("MAX_CONCURRENT_DOWNLOADS", 3)
CONCURRENT_FRAGMENTS = _int("CONCURRENT_FRAGMENTS", 4)
DOWNLOAD_FORMAT = _str("DOWNLOAD_FORMAT", "bestvideo+bestaudio/best")
FORMAT_SORT = _csv_str("FORMAT_SORT", ["res:1080"])
TEMP_DIR = _str("TEMP_DIR", "temp")
SESSION_DB = _str("SESSION_DB", "session.db")
DOWNLOAD_MAX_ATTEMPTS = _int("DOWNLOAD_MAX_ATTEMPTS", 3)
DOWNLOAD_RETRY_BACKOFFS = _csv_int("DOWNLOAD_RETRY_BACKOFFS", [2, 8])
DOWNLOAD_ATTEMPT_TIMEOUT = _int("DOWNLOAD_ATTEMPT_TIMEOUT", 600)
JOB_TIMEOUT = _int("JOB_TIMEOUT", 1800)

# Phone numbers (digits only, no +, spaces, or dashes) allowed to use the bot.
# Override via .env: ALLOWED_SENDERS=11234312,41092103921
ALLOWED_SENDERS: set[str] = _csv_set("ALLOWED_SENDERS", set())
