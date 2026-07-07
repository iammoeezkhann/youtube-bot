from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

from bot.channel_sync import extract_channel_url, sync_channel
from bot.config import AppConfig
from bot.telegram_notify import next_upload_slot_utc, setup_notifier
from bot.youtube_download import download_youtube_video, extract_youtube_url

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
PENDING_URL_KEY = "pending_youtube_url"


def _sanitize_filename(value: str) -> str:
    import re

    cleaned = re.sub(r'[<>:"/\\|?*]', "", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:80] or "video"


def _is_allowed_user(config: AppConfig, user_id: int | None) -> bool:
    if user_id is None:
        return False
    return user_id in config.telegram.allowed_user_ids


def _file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.application.bot_data["config"]
    user = update.effective_user
    if not _is_allowed_user(config, user.id if user else None):
        await update.message.reply_text("You are not authorized to use this bot.")
        return

    await update.message.reply_text(
        "Send me a video, a link, or sync a channel/profile.\n\n"
        "Video files:\n"
        "- Send with a caption to set the title\n\n"
        "YouTube links:\n"
        "1. Paste the link\n"
        "2. Send a caption/title\n"
        "3. I download and queue it\n\n"
        "Channel copy (re-upload randomly on schedule):\n"
        "/channel <url>\n"
        "Supports YouTube, TikTok, Instagram, Facebook profiles\n\n"
        "Commands: /status /channel /cancel"
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.application.bot_data["config"]
    user = update.effective_user
    if not _is_allowed_user(config, user.id if user else None):
        await update.message.reply_text("You are not authorized to use this bot.")
        return

    if context.user_data.pop(PENDING_URL_KEY, None):
        await update.message.reply_text("Cancelled. Send a new link whenever you are ready.")
    else:
        await update.message.reply_text("Nothing to cancel.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.application.bot_data["config"]
    user = update.effective_user
    if not _is_allowed_user(config, user.id if user else None):
        await update.message.reply_text("You are not authorized to use this bot.")
        return

    watch_folder = config.watch_folder
    if not watch_folder.exists():
        pending = 0
    else:
        pending = sum(
            1
            for path in watch_folder.iterdir()
            if path.is_file() and path.suffix.lower() in config.video_extensions
        )

    source_count = 0
    if config.sources_folder.exists():
        source_count = sum(
            1
            for path in config.sources_folder.rglob("*")
            if path.is_file()
            and path.suffix.lower() in config.video_extensions
            and not path.name.startswith(".")
        )

    times = ", ".join(config.schedule.times) if config.schedule.times else "not set"
    next_slot = next_upload_slot_utc(config.schedule.times)
    pending_link = context.user_data.get(PENDING_URL_KEY)

    enabled = []
    if config.platforms.youtube.enabled:
        enabled.append("YouTube")
    if config.platforms.instagram.enabled:
        enabled.append("Instagram")
    if config.platforms.facebook.enabled:
        enabled.append("Facebook")
    if config.platforms.tiktok.enabled:
        enabled.append("TikTok")
    platform_line = ", ".join(enabled) or "none enabled"

    message = (
        f"Pending inbox videos: {pending}\n"
        f"Channel pool videos: {source_count}\n"
        f"Platforms: {platform_line}\n"
        f"Pick strategy: {config.upload.pick_strategy}\n"
        f"Upload schedule (UTC): {times}\n"
        f"Next upload slot: {next_slot}\n"
        f"Watch folder: {watch_folder}\n"
        f"Sources folder: {config.sources_folder}"
    )
    if pending_link:
        message += f"\n\nWaiting for caption for link:\n{pending_link}"

    await update.message.reply_text(message)


async def _queue_downloaded_file(
    message,
    config: AppConfig,
    filepath: Path,
    title: str,
    width: int | None = None,
    height: int | None = None,
    vcodec: str = "",
    filesize_mb: float | None = None,
    from_youtube_link: bool = False,
) -> None:
    size_mb = filesize_mb if filesize_mb is not None else _file_size_mb(filepath)
    next_slot = next_upload_slot_utc(config.schedule.times)
    resolution = ""
    if width and height:
        resolution = f"\nQuality: {width}x{height}"
    if vcodec:
        resolution += f" ({vcodec})"

    note = ""
    if from_youtube_link:
        note = (
            "\n\nNote: Link downloads are re-compressed by YouTube on upload, "
            "so they will not match the original 1:1. For best quality, send the "
            "original video file from your phone. HD on YouTube may take 30-60 min "
            "to finish processing after upload."
        )

    await message.reply_text(
        "Download complete.\n\n"
        f"Title: {title}\n"
        f"File: {filepath.name}\n"
        f"Size: {size_mb:.1f} MB{resolution}\n\n"
        "Queued for upload to all enabled platforms.\n"
        f"Next upload slot (UTC): {next_slot}\n\n"
        "I will message you again when uploads finish."
        f"{note}"
    )


async def _download_link_with_caption(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    caption: str,
) -> None:
    config: AppConfig = context.application.bot_data["config"]
    message = update.message
    title = caption.strip()

    if not title:
        await message.reply_text("Caption cannot be empty. Send a title for this video.")
        return

    await message.reply_text(
        f"Caption saved: {title}\n\n"
        "Downloading from YouTube now... please wait."
    )

    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: download_youtube_video(
                url,
                config.watch_folder,
                title,
                config.download.quality,
                config.download.cookies_file,
            ),
        )
    except Exception as error:
        logger.exception("YouTube download failed for %s", url)
        await message.reply_text(
            "Download failed.\n\n"
            f"Title: {title}\n"
            f"Reason: {error}\n\n"
            "The video was NOT queued."
        )
        return

    await _queue_downloaded_file(
        message,
        config,
        result.filepath,
        result.title,
        result.width,
        result.height,
        result.vcodec,
        result.filesize_mb,
        from_youtube_link=True,
    )


async def cmd_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.application.bot_data["config"]
    user = update.effective_user
    if not _is_allowed_user(config, user.id if user else None):
        await update.message.reply_text("You are not authorized to use this bot.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /channel <profile or channel URL>\n\n"
            "Examples:\n"
            "https://youtube.com/@channelname\n"
            "https://tiktok.com/@username\n"
            "https://instagram.com/username\n"
            "https://facebook.com/pagename"
        )
        return

    url = extract_channel_url(" ".join(context.args))
    if not url:
        await update.message.reply_text("That does not look like a supported channel/profile URL.")
        return

    max_videos = config.platforms.channel.max_videos_per_channel
    await update.message.reply_text(
        f"Syncing up to {max_videos} videos from:\n{url}\n\nThis may take a while..."
    )

    try:
        output_dir, count = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: sync_channel(
                url,
                config.sources_folder,
                config.download,
                max_videos,
            ),
        )
    except Exception as error:
        logger.exception("Channel sync failed for %s", url)
        await update.message.reply_text(f"Channel sync failed.\n\nReason: {error}")
        return

    await update.message.reply_text(
        f"Channel sync complete.\n\n"
        f"URL: {url}\n"
        f"Downloaded: {count} video(s)\n"
        f"Saved to: {output_dir}\n\n"
        f"Set upload.pick_strategy: random in config.yaml to re-upload "
        f"random videos from this pool on each scheduled run."
    )


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.application.bot_data["config"]
    message = update.message
    user = update.effective_user

    if message is None:
        return

    if not _is_allowed_user(config, user.id if user else None):
        await message.reply_text("You are not authorized to use this bot.")
        return

    file_obj = message.video or message.document
    if file_obj is None:
        await message.reply_text("Please send a video file.")
        return

    if message.document and message.document.mime_type:
        if not message.document.mime_type.startswith("video/"):
            await message.reply_text("That file is not a video.")
            return

    caption = (message.caption or "").strip()
    if not caption:
        await message.reply_text(
            "Please send the video again with a caption.\n"
            "The caption will be used as the YouTube title."
        )
        return

    config.watch_folder.mkdir(parents=True, exist_ok=True)

    if message.video:
        extension = ".mp4"
    elif message.document and message.document.file_name:
        extension = Path(message.document.file_name).suffix.lower()
        if extension not in VIDEO_EXTENSIONS:
            extension = ".mp4"
    else:
        extension = ".mp4"

    base_name = _sanitize_filename(caption)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{base_name}{extension}"
    destination = config.watch_folder / filename

    if destination.exists():
        destination = config.watch_folder / f"{timestamp}_{file_obj.file_unique_id}{extension}"

    await message.reply_text("Downloading your video from Telegram...")
    telegram_file = await context.bot.get_file(file_obj.file_id)
    await telegram_file.download_to_drive(custom_path=str(destination))

    title_file = destination.with_suffix(".title.txt")
    title_file.write_text(caption[:100], encoding="utf-8")

    logger.info("Telegram video saved: %s (user %s)", destination.name, user.id if user else "unknown")
    await _queue_downloaded_file(message, config, destination, caption[:100])


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None or not message.text:
        return

    config: AppConfig = context.application.bot_data["config"]
    user = update.effective_user
    if not _is_allowed_user(config, user.id if user else None):
        await message.reply_text("You are not authorized to use this bot.")
        return

    text = message.text.strip()
    url = extract_youtube_url(text)

    if url and context.user_data.get(PENDING_URL_KEY):
        context.user_data[PENDING_URL_KEY] = url
        await message.reply_text(
            "New link saved.\n\n"
            f"{url}\n\n"
            "Send the caption/title for YouTube."
        )
        return

    if context.user_data.get(PENDING_URL_KEY):
        pending_url = context.user_data.pop(PENDING_URL_KEY)
        await _download_link_with_caption(update, context, pending_url, text)
        return

    if url:
        context.user_data[PENDING_URL_KEY] = url
        await message.reply_text(
            "Link received.\n\n"
            f"{url}\n\n"
            "What caption/title should I use on YouTube?\n"
            "Send it now, or /cancel to abort."
        )
        return

    channel_url = extract_channel_url(text)
    if channel_url and not url:
        await update.message.reply_text(
            "To sync a channel/profile, use:\n"
            f"/channel {channel_url}"
        )
        return

    await message.reply_text(
        "Send a video with a caption, a YouTube/Shorts link, or use /channel <url> "
        "to copy a channel for random re-uploads."
    )


def build_telegram_application(config: AppConfig) -> Application:
    if not config.telegram.bot_token:
        raise ValueError(
            "Telegram bot token is missing. Set telegram.bot_token in config.yaml "
            "or TELEGRAM_BOT_TOKEN in the environment."
        )
    if not config.telegram.allowed_user_ids:
        raise ValueError(
            "telegram.allowed_user_ids is empty. Add your Telegram user ID for security."
        )

    async def post_init(application: Application) -> None:
        setup_notifier(
            application.bot,
            config.telegram.allowed_user_ids,
            asyncio.get_running_loop(),
        )

    request = HTTPXRequest(
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=60.0,
    )

    application = (
        Application.builder()
        .token(config.telegram.bot_token)
        .request(request)
        .post_init(post_init)
        .build()
    )
    application.bot_data["config"] = config
    application.add_handler(CommandHandler("channel", cmd_channel))
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("cancel", cmd_cancel))
    application.add_handler(
        MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video)
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return application


def start_telegram_polling(config: AppConfig) -> None:
    application = build_telegram_application(config)
    logger.info("Connecting to Telegram...")
    application.run_polling(drop_pending_updates=True)
    logger.info("Telegram bot stopped.")
