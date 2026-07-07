from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telegram import Bot

logger = logging.getLogger(__name__)

_bot: Bot | None = None
_loop: asyncio.AbstractEventLoop | None = None
_chat_ids: tuple[int, ...] = ()


def setup_notifier(bot: Bot, chat_ids: tuple[int, ...], loop: asyncio.AbstractEventLoop) -> None:
    global _bot, _loop, _chat_ids
    _bot = bot
    _chat_ids = chat_ids
    _loop = loop
    logger.info("Telegram upload notifications enabled for %s user(s)", len(chat_ids))


def send_message(text: str) -> None:
    if _bot is None or _loop is None or not _chat_ids:
        return

    async def _send() -> None:
        for chat_id in _chat_ids:
            try:
                await _bot.send_message(chat_id=chat_id, text=text)
            except Exception:
                logger.exception("Failed to send Telegram message to %s", chat_id)

    asyncio.run_coroutine_threadsafe(_send(), _loop)


def notify_download_complete(title: str, filename: str, size_mb: float, next_slot: str) -> None:
    send_message(
        "Download complete.\n\n"
        f"Title: {title}\n"
        f"File: {filename}\n"
        f"Size: {size_mb:.1f} MB\n\n"
        f"Queued for YouTube upload.\n"
        f"Next upload slot (UTC): {next_slot}"
    )


def notify_download_failed(title: str, error: str) -> None:
    send_message(
        "Download failed.\n\n"
        f"Title: {title}\n"
        f"Reason: {error}\n\n"
        "The video was NOT queued."
    )


def notify_upload_started(title: str, filename: str) -> None:
    send_message(f"Uploading to YouTube now...\n\nTitle: {title}\nFile: {filename}")


def notify_upload_complete(title: str, video_id: str) -> None:
    send_message(
        "YouTube upload complete.\n\n"
        f"Title: {title}\n"
        f"Link: https://youtu.be/{video_id}"
    )


def notify_upload_failed(title: str, filename: str, error: str) -> None:
    send_message(
        "YouTube upload failed.\n\n"
        f"Title: {title}\n"
        f"File: {filename}\n"
        f"Reason: {error}"
    )


def _platform_label(platform: PlatformUploader) -> str:
    return platform.name.capitalize()


def notify_upload_started_multi(
    title: str, filename: str, platforms: list
) -> None:
    names = ", ".join(_platform_label(p) for p in platforms) or "no platforms"
    send_message(
        f"Uploading now to: {names}\n\nTitle: {title}\nFile: {filename}"
    )


def notify_upload_complete_multi(title: str, results: dict[str, str]) -> None:
    lines = [f"Upload complete.\n\nTitle: {title}"]
    for platform, value in results.items():
        if value.startswith("FAILED:"):
            lines.append(f"\n{platform.capitalize()}: failed — {value[7:].strip()}")
        else:
            lines.append(f"\n{platform.capitalize()}: {value}")
    send_message("".join(lines))


def notify_upload_failed_multi(title: str, filename: str, error: str) -> None:
    send_message(
        "Upload failed on all platforms.\n\n"
        f"Title: {title}\n"
        f"File: {filename}\n"
        f"Reason: {error}"
    )


def next_upload_slot_utc(times: tuple[str, ...]) -> str:
    if not times:
        return "not configured"

    now = datetime.now(timezone.utc)
    candidates: list[datetime] = []

    for value in times:
        hour, minute = (int(part) for part in value.split(":", 1))
        slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if slot <= now:
            slot += timedelta(days=1)
        candidates.append(slot)

    return min(candidates).strftime("%Y-%m-%d %H:%M UTC")
