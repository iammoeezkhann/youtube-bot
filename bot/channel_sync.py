from __future__ import annotations

import json
import logging
import random
import re
from datetime import datetime, timezone
from pathlib import Path

import yt_dlp

from bot.config import DownloadConfig
from bot.youtube_download import (
    QUALITY_FORMATS,
    _build_ytdlp_options,
    _ffmpeg_available,
    _resolve_downloaded_file,
)

logger = logging.getLogger(__name__)

CHANNEL_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:"
    r"youtube\.com/(?:@[\w.-]+|channel/[\w-]+|c/[\w.-]+|user/[\w.-]+)|"
    r"tiktok\.com/@[\w.-]+|"
    r"instagram\.com/[\w.-]+|"
    r"facebook\.com/[\w.-]+"
    r")[^\s]*",
    re.IGNORECASE,
)


def extract_channel_url(text: str) -> str | None:
    match = CHANNEL_URL_RE.search(text.strip())
    if not match:
        return None
    return match.group(0).rstrip(").,]")


def _channel_slug(url: str) -> str:
    cleaned = url.rstrip("/").split("?")[0]
    parts = cleaned.rstrip("/").split("/")
    for part in reversed(parts):
        if part and part not in {"www.youtube.com", "youtube.com", "tiktok.com", "instagram.com", "facebook.com"}:
            return re.sub(r"[^\w.-]", "_", part)[:60]
    return "channel"


def sync_channel(
    url: str,
    sources_root: Path,
    download_config: DownloadConfig,
    max_videos: int = 50,
) -> tuple[Path, int]:
    """Download up to max_videos from a channel/profile into sources/<slug>/."""
    sources_root.mkdir(parents=True, exist_ok=True)
    slug = _channel_slug(url)
    output_dir = sources_root / slug
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_template = str(output_dir / f"{timestamp}_%(id)s.%(ext)s")

    quality_key = download_config.quality if download_config.quality in QUALITY_FORMATS else "best"
    if _ffmpeg_available():
        format_selector = QUALITY_FORMATS[quality_key]
        merge = True
    else:
        format_selector = "best[ext=mp4]/best"
        merge = False

    options = _build_ytdlp_options(
        output_template,
        format_selector,
        merge,
        download_config.cookies_file,
    )
    options["noplaylist"] = False
    options["playlistend"] = max_videos
    options["ignoreerrors"] = True
    options["writeinfojson"] = False

    downloaded = 0
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise RuntimeError("Could not read channel/profile info from that URL.")

        entries = info.get("entries") or [info]
        for entry in entries:
            if not entry:
                continue
            try:
                filepath = _resolve_downloaded_file(output_dir, timestamp, entry)
                if filepath.exists():
                    downloaded += 1
            except Exception:
                continue

    meta = {
        "url": url,
        "slug": slug,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "video_count": downloaded,
    }
    (output_dir / ".channel.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    logger.info("Channel sync %s -> %s videos in %s", url, downloaded, output_dir)
    return output_dir, downloaded


def list_source_videos(
    sources_root: Path,
    watch_folder: Path,
    video_extensions: tuple[str, ...],
) -> list[Path]:
    videos: list[Path] = []

    def collect(folder: Path) -> None:
        if not folder.exists():
            return
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix.lower() in video_extensions:
                if path.name.startswith("."):
                    continue
                videos.append(path)

    collect(watch_folder)
    if sources_root.exists():
        collect(sources_root)

    return videos


def pick_random_video(
    sources_root: Path,
    watch_folder: Path,
    video_extensions: tuple[str, ...],
) -> Path | None:
    pool = list_source_videos(sources_root, watch_folder, video_extensions)
    if not pool:
        return None
    return random.choice(pool)
