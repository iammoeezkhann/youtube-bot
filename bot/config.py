from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PRIVACY_MAP = {
    "public": "public",
    "unlisted": "unlisted",
    "private": "private",
}


@dataclass(frozen=True)
class DownloadConfig:
    quality: str
    cookies_file: str


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool
    bot_token: str
    allowed_user_ids: tuple[int, ...]


@dataclass(frozen=True)
class ScheduleConfig:
    type: str
    hours: int
    minutes: int
    cron: str
    timezone: str
    times: tuple[str, ...]


@dataclass(frozen=True)
class UploadConfig:
    mode: str
    privacy: str
    category_id: str
    title_template: str
    description_template: str
    tags: list[str]
    thumbnail: str
    recycle_uploads: bool
    pick_strategy: str


@dataclass(frozen=True)
class YouTubePlatformConfig:
    enabled: bool


@dataclass(frozen=True)
class PlatformEnabled:
    enabled: bool


@dataclass(frozen=True)
class MetaPlatformConfig:
    access_token: str
    instagram_account_id: str
    facebook_page_id: str


@dataclass(frozen=True)
class TikTokPlatformConfig:
    enabled: bool
    access_token: str
    client_key: str
    client_secret: str
    redirect_uri: str
    post_mode: str
    privacy_level: str


@dataclass(frozen=True)
class ChannelSyncConfig:
    max_videos_per_channel: int


@dataclass(frozen=True)
class PlatformsConfig:
    youtube: YouTubePlatformConfig
    instagram: PlatformEnabled
    facebook: PlatformEnabled
    tiktok: TikTokPlatformConfig
    meta: MetaPlatformConfig
    channel: ChannelSyncConfig


@dataclass(frozen=True)
class PathsConfig:
    client_secret: Path
    token: Path
    tiktok_app: Path
    tiktok_token: Path
    state_file: Path
    log_file: Path
    lock_file: Path


@dataclass(frozen=True)
class AppConfig:
    watch_folder: Path
    sources_folder: Path
    uploaded_subfolder: str
    video_extensions: tuple[str, ...]
    schedule: ScheduleConfig
    upload: UploadConfig
    download: DownloadConfig
    telegram: TelegramConfig
    platforms: PlatformsConfig
    paths: PathsConfig


def _require(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ValueError(f"Missing required config key: {key}")
    return data[key]


def load_config(config_path: Path) -> AppConfig:
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    schedule_raw = _require(raw, "schedule")
    upload_raw = _require(raw, "upload")
    paths_raw = _require(raw, "paths")

    privacy = str(upload_raw.get("privacy", "public")).lower()
    if privacy not in PRIVACY_MAP:
        raise ValueError("upload.privacy must be public, unlisted, or private")

    mode = str(upload_raw.get("mode", "one")).lower()
    if mode not in {"one", "all"}:
        raise ValueError("upload.mode must be one or all")

    schedule_type = str(schedule_raw.get("type", "interval")).lower()
    if schedule_type not in {"interval", "cron", "daily_times"}:
        raise ValueError("schedule.type must be interval, cron, or daily_times")

    timezone = str(schedule_raw.get("timezone", "UTC"))
    times = tuple(str(value) for value in schedule_raw.get("times", []))
    if schedule_type == "daily_times" and not times:
        raise ValueError("schedule.times is required when schedule.type is daily_times")
    for value in times:
        parts = value.split(":")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError(f"Invalid time format '{value}'. Use HH:MM (24-hour).")
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"Invalid time '{value}'. Hour must be 0-23, minute 0-59.")

    root = config_path.parent
    paths = PathsConfig(
        client_secret=(root / paths_raw["client_secret"]).resolve(),
        token=(root / paths_raw["token"]).resolve(),
        tiktok_app=(root / paths_raw.get("tiktok_app", "credentials/tiktok_app.json")).resolve(),
        tiktok_token=(root / paths_raw.get("tiktok_token", "credentials/tiktok_token.json")).resolve(),
        state_file=(root / paths_raw["state_file"]).resolve(),
        log_file=(root / paths_raw["log_file"]).resolve(),
        lock_file=(root / paths_raw["lock_file"]).resolve(),
    )

    extensions = tuple(
        ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        for ext in raw.get("video_extensions", [".mp4"])
    )

    telegram_raw = raw.get("telegram", {})
    bot_token = str(telegram_raw.get("bot_token", "")).strip() or os.getenv(
        "TELEGRAM_BOT_TOKEN", ""
    ).strip()
    allowed_user_ids = tuple(
        int(value) for value in telegram_raw.get("allowed_user_ids", [])
    )

    download_raw = raw.get("download", {})
    quality = str(download_raw.get("quality", "best")).lower()
    if quality not in {"best", "1080p", "720p"}:
        raise ValueError("download.quality must be best, 1080p, or 720p")
    cookies_file = str(download_raw.get("cookies_file", "")).strip()

    pick_strategy = str(upload_raw.get("pick_strategy", "fifo")).lower()
    if pick_strategy not in {"fifo", "random"}:
        raise ValueError("upload.pick_strategy must be fifo or random")

    platforms_raw = raw.get("platforms", {})
    youtube_raw = platforms_raw.get("youtube", {})
    instagram_raw = platforms_raw.get("instagram", {})
    facebook_raw = platforms_raw.get("facebook", {})
    tiktok_raw = platforms_raw.get("tiktok", {})
    meta_raw = platforms_raw.get("meta", platforms_raw)
    channel_raw = platforms_raw.get("channel", {})

    meta_token = str(meta_raw.get("access_token", "")).strip() or os.getenv(
        "META_ACCESS_TOKEN", ""
    ).strip()
    tiktok_token = str(tiktok_raw.get("access_token", "")).strip() or os.getenv(
        "TIKTOK_ACCESS_TOKEN", ""
    ).strip()
    tiktok_client_key = str(tiktok_raw.get("client_key", "")).strip() or os.getenv(
        "TIKTOK_CLIENT_KEY", ""
    ).strip()
    tiktok_client_secret = str(tiktok_raw.get("client_secret", "")).strip() or os.getenv(
        "TIKTOK_CLIENT_SECRET", ""
    ).strip()
    tiktok_redirect = str(tiktok_raw.get("redirect_uri", "http://127.0.0.1:8765/callback")).strip()
    tiktok_post_mode = str(tiktok_raw.get("post_mode", "inbox")).lower()
    if tiktok_post_mode not in {"inbox", "direct"}:
        raise ValueError("platforms.tiktok.post_mode must be inbox or direct")
    tiktok_privacy = str(tiktok_raw.get("privacy_level", "PUBLIC_TO_EVERYONE")).upper()

    platforms = PlatformsConfig(
        youtube=YouTubePlatformConfig(enabled=bool(youtube_raw.get("enabled", True))),
        instagram=PlatformEnabled(enabled=bool(instagram_raw.get("enabled", False))),
        facebook=PlatformEnabled(enabled=bool(facebook_raw.get("enabled", False))),
        tiktok=TikTokPlatformConfig(
            enabled=bool(tiktok_raw.get("enabled", False)),
            access_token=tiktok_token,
            client_key=tiktok_client_key,
            client_secret=tiktok_client_secret,
            redirect_uri=tiktok_redirect,
            post_mode=tiktok_post_mode,
            privacy_level=tiktok_privacy,
        ),
        meta=MetaPlatformConfig(
            access_token=meta_token,
            instagram_account_id=str(meta_raw.get("instagram_account_id", "")).strip(),
            facebook_page_id=str(meta_raw.get("facebook_page_id", "")).strip(),
        ),
        channel=ChannelSyncConfig(
            max_videos_per_channel=int(channel_raw.get("max_videos_per_channel", 50)),
        ),
    )

    sources_folder = Path(
        raw.get("sources_folder", str(Path(_require(raw, "watch_folder")) / "sources"))
    ).expanduser().resolve()

    return AppConfig(
        watch_folder=Path(_require(raw, "watch_folder")).expanduser().resolve(),
        sources_folder=sources_folder,
        uploaded_subfolder=str(raw.get("uploaded_subfolder", "uploaded")),
        video_extensions=extensions,
        schedule=ScheduleConfig(
            type=schedule_type,
            hours=int(schedule_raw.get("hours", 1)),
            minutes=int(schedule_raw.get("minutes", 0)),
            cron=str(schedule_raw.get("cron", "0 * * * *")),
            timezone=timezone,
            times=times,
        ),
        upload=UploadConfig(
            mode=mode,
            privacy=privacy,
            category_id=str(upload_raw.get("category_id", "22")),
            title_template=str(upload_raw.get("title_template", "{filename}")),
            description_template=str(
                upload_raw.get("description_template", "Uploaded automatically.")
            ),
            tags=[str(tag) for tag in upload_raw.get("tags", [])],
            thumbnail=str(upload_raw.get("thumbnail", "")),
            recycle_uploads=bool(upload_raw.get("recycle_uploads", True)),
            pick_strategy=pick_strategy,
        ),
        download=DownloadConfig(quality=quality, cookies_file=cookies_file),
        telegram=TelegramConfig(
            enabled=bool(telegram_raw.get("enabled", False)),
            bot_token=bot_token,
            allowed_user_ids=allowed_user_ids,
        ),
        platforms=platforms,
        paths=paths,
    )
