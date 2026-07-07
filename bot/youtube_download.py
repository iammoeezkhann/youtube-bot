from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yt_dlp

logger = logging.getLogger(__name__)

YOUTUBE_URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:shorts/|watch\?v=|live/|embed/)|youtu\.be/)[^\s]+",
    re.IGNORECASE,
)

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}

QUALITY_FORMATS = {
    "best": "bestvideo*+bestaudio/b",
    "1080p": "bestvideo[height<=1080]+bestaudio/b",
    "720p": "bestvideo[height<=720]+bestaudio/b",
}


class DownloadResult:
    def __init__(
        self,
        filepath: Path,
        title: str,
        width: int | None = None,
        height: int | None = None,
        vcodec: str = "",
        filesize_mb: float = 0.0,
    ) -> None:
        self.filepath = filepath
        self.title = title
        self.width = width
        self.height = height
        self.vcodec = vcodec
        self.filesize_mb = filesize_mb


def extract_youtube_url(text: str) -> str | None:
    match = YOUTUBE_URL_RE.search(text.strip())
    if not match:
        return None
    url = match.group(0).rstrip(").,]")
    return url


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _clean_error(message: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", message).strip()


def _resolve_downloaded_file(output_dir: Path, timestamp: str, info: dict) -> Path:
    filepath = info.get("filepath")
    if filepath:
        path = Path(filepath)
        if path.exists() and path.suffix.lower() in VIDEO_SUFFIXES:
            return path

    merged = info.get("requested_downloads")
    if merged:
        for item in merged:
            candidate = Path(item.get("filepath", ""))
            if candidate.exists() and candidate.suffix.lower() in VIDEO_SUFFIXES:
                return candidate

    candidates = sorted(
        (
            path
            for path in output_dir.glob(f"{timestamp}_*")
            if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
        ),
        key=lambda path: path.stat().st_size,
        reverse=True,
    )
    if candidates:
        return candidates[0]

    raise RuntimeError("Download finished but the video file was not found.")


def _build_ytdlp_options(
    output_template: str,
    format_selector: str,
    merge: bool,
    cookies_file: str,
) -> dict:
    options = {
        "format": format_selector,
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
        "noplaylist": True,
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
        "format_sort": [
            "quality",
            "res:",
            "fps:",
            "hdr:12",
            "vcodec:vp9",
            "vcodec:av1",
            "vcodec:h264",
            "size:",
        ],
        "extractor_args": {
            "youtube": {
                # These clients often expose higher-quality streams than the default web client.
                "player_client": ["android_vr", "android", "web", "default"],
            }
        },
    }
    if merge:
        options["merge_output_format"] = "mp4"

    if cookies_file:
        cookie_path = Path(cookies_file).expanduser()
        if cookie_path.exists():
            options["cookiefile"] = str(cookie_path.resolve())
        else:
            logger.warning("Cookies file not found: %s", cookie_path)

    return options


def _download_with_format(
    url: str,
    output_dir: Path,
    title: str,
    output_template: str,
    format_selector: str,
    merge: bool = False,
    cookies_file: str = "",
) -> DownloadResult:
    options = _build_ytdlp_options(output_template, format_selector, merge, cookies_file)

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise RuntimeError("Could not read video info from that link.")

        timestamp = Path(output_template).name.split("_", 1)[0]
        filepath = _resolve_downloaded_file(output_dir, timestamp, info)

    final_title = title.strip() or str(info.get("title", filepath.stem))
    title_file = filepath.with_suffix(".title.txt")
    title_file.write_text(final_title[:100], encoding="utf-8")

    width = info.get("width")
    height = info.get("height")
    vcodec = str(info.get("vcodec") or "")
    size_mb = filepath.stat().st_size / (1024 * 1024)
    logger.info(
        "Downloaded YouTube video: %s -> %s (%sx%s, %s, %.1f MB)",
        url,
        filepath.name,
        width or "?",
        height or "?",
        vcodec or "unknown codec",
        size_mb,
    )
    return DownloadResult(
        filepath=filepath,
        title=final_title[:100],
        width=width,
        height=height,
        vcodec=vcodec,
        filesize_mb=size_mb,
    )


def download_youtube_video(
    url: str,
    output_dir: Path,
    title: str,
    quality: str = "best",
    cookies_file: str = "",
) -> DownloadResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_template = str(output_dir / f"{timestamp}_%(id)s.%(ext)s")

    quality_key = quality if quality in QUALITY_FORMATS else "best"
    attempts: list[tuple[str, bool]] = []

    if _ffmpeg_available():
        attempts.append((QUALITY_FORMATS[quality_key], True))
        if quality_key != "best":
            attempts.append((QUALITY_FORMATS["best"], True))
    else:
        attempts.append(
            (
                "best[ext=mp4][acodec!=none][vcodec!=none]/best[ext=mp4]/best",
                False,
            )
        )

    attempts.append(("best[ext=mp4]/best", False))

    last_error: Exception | None = None
    for format_selector, merge in attempts:
        try:
            return _download_with_format(
                url,
                output_dir,
                title,
                output_template,
                format_selector,
                merge,
                cookies_file,
            )
        except Exception as error:
            last_error = error
            logger.warning("Download attempt failed (%s): %s", format_selector, error)

    message = _clean_error(str(last_error or "Unknown download error"))
    if "ffmpeg" in message.lower() and not _ffmpeg_available():
        raise RuntimeError(
            "ffmpeg is not installed. Run: winget install Gyan.FFmpeg "
            "Then restart the terminal and try again."
        ) from last_error

    raise RuntimeError(message) from last_error
