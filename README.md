# yt-bot

A WhatsApp bot that downloads YouTube videos and sends them back as files. Mention `@ytbot` with a YouTube link and the bot replies with the video.

## How it works

1. Send (or reply to) a message containing `@ytbot` and one or more YouTube URLs
2. The bot queues the downloads and replies with a confirmation listing the titles
3. As each download finishes, the video is sent back as a document — the others continue downloading in parallel
4. A summary is sent at the end if any URL failed

Supports standard YouTube URLs, Shorts, embeds, `youtu.be` short links, and `share.google/...` links (resolved via redirect / Google search HTML).

The `@ytbot` trigger must be present in your message; bare links are ignored. The trigger can be in the reply text while the link sits in the quoted message.

## Requirements

- Python 3.14+ (uses `uuid.uuid7()`)
- [uv](https://github.com/astral-sh/uv)

## Setup

```bash
uv sync
cp .env.example .env
# edit .env and set ALLOWED_SENDERS=<your-number>,<other-numbers>
```

`ALLOWED_SENDERS` defaults to an empty set, so without a `.env` the bot will reject every incoming message. Numbers are digits only — no `+`, spaces, or dashes.

## Running

```bash
uv run yt-bot
```

On first run a QR code is printed in the terminal — scan it with WhatsApp (Linked Devices). The session is saved to `session.db`, so subsequent runs skip the QR.

Flags:
- `--rescan` — delete the saved session and force a fresh QR scan

Ctrl+C performs a graceful shutdown: active downloads are cancelled (10s grace window), then the process exits. A second Ctrl+C bypasses the grace period.

## Configuration

All settings have defaults in `yt_bot/config.py` and can be overridden by environment variables (typically via `.env`). See `.env.example` for the full list.

| Setting | Default | Description |
|---|---|---|
| `ALLOWED_SENDERS` | _(empty)_ | Comma-separated phone numbers allowed to use the bot. Self-messages bypass this check. |
| `MAX_QUEUE_SIZE` | `3` | Max pending message batches before the bot tells senders it's busy |
| `MAX_CONCURRENT_JOBS` | `2` | Number of message batches processed in parallel |
| `MAX_CONCURRENT_DOWNLOADS` | `3` | Parallel video downloads within one batch |
| `CONCURRENT_FRAGMENTS` | `4` | yt-dlp parallel fragment streams per video |
| `DOWNLOAD_FORMAT` | `bestvideo+bestaudio/best` | yt-dlp format string |
| `FORMAT_SORT` | `res:1080` | yt-dlp format sort preference |
| `DOWNLOAD_MAX_ATTEMPTS` | `3` | Total attempts per URL on transient failures (1 = no retry) |
| `DOWNLOAD_RETRY_BACKOFFS` | `2,8` | Seconds to wait before each retry |
| `DOWNLOAD_ATTEMPT_TIMEOUT` | `600` | Per-attempt wall-clock timeout (seconds) |
| `JOB_TIMEOUT` | `1800` | Overall wall-clock cap per job (seconds) |
| `TEMP_DIR` | `temp` | Working directory for in-progress downloads |
| `SESSION_DB` | `session.db` | WhatsApp session credentials path |

Permanent errors (private/removed/geo-blocked/unsupported URLs) are not retried.

Each job uses a unique subdirectory under `TEMP_DIR/`, wiped on completion — partials, fragment files, and any leftover artifacts are cleaned automatically. The whole `TEMP_DIR/` is also wiped on bot startup as a safety net.

## Tests

```bash
uv run pytest
```

86 tests cover the URL/trigger regexes, message handler routing, retry/cancel/timeout logic, error classification, and shutdown coordination.

## Project structure

```
yt_bot/
  bot.py         # Entry point — WhatsApp client, signal handling, graceful shutdown
  handler.py     # Parses incoming messages, validates trigger + sender, enqueues jobs
  worker.py      # Job queue consumer, per-job temp dirs, ShutdownContext
  downloader.py  # yt-dlp wrapper with retry, per-attempt timeout, share.google resolution
  config.py      # Settings with .env overrides
  logger.py      # Logging setup
tests/           # pytest suite
docs/            # architecture & design notes
```

For internals — threading model, retry/cancel coordination, shutdown flow — see [docs/architecture.md](docs/architecture.md).
