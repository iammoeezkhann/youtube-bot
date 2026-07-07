from __future__ import annotations

import logging
import mimetypes
import shutil
import time
from pathlib import Path

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from bot.config import AppConfig
from bot.state import UploadState
from bot.telegram_notify import (
    notify_upload_complete,
    notify_upload_failed,
    notify_upload_started,
)

logger = logging.getLogger(__name__)


class UploadLockError(RuntimeError):
    pass


class YouTubeUploader:
    def __init__(self, config: AppConfig, youtube_service, state: UploadState) -> None:
        self.config = config
        self.youtube = youtube_service
        self.state = state

    def run_once(self) -> int:
        self._acquire_lock()
        try:
            picks = self._pick_videos()
            if not picks:
                logger.info(
                    "No new videos in %s and nothing to recycle",
                    self.config.watch_folder,
                )
                return 0

            uploaded_count = 0
            for video_path, is_recycle in picks:
                title = self._build_title(video_path)
                try:
                    if is_recycle:
                        logger.info("Recycling previously uploaded video: %s", video_path.name)
                    notify_upload_started(title, video_path.name)
                    video_id = self._upload_video(video_path, title, recycle=is_recycle)
                    notify_upload_complete(title, video_id)
                    uploaded_count += 1
                except HttpError as error:
                    logger.exception("YouTube API error for %s: %s", video_path.name, error)
                    notify_upload_failed(title, video_path.name, str(error))
                except Exception as error:
                    logger.exception("Failed to upload %s", video_path.name)
                    notify_upload_failed(title, video_path.name, str(error))
            return uploaded_count
        finally:
            self._release_lock()

    def _pick_videos(self) -> list[tuple[Path, bool]]:
        pending = self._pending_new_videos()
        if pending:
            if self.config.upload.mode == "one":
                pending = pending[:1]
            return [(path, False) for path in pending]

        if not self.config.upload.recycle_uploads:
            return []

        recycled = self.state.next_recycle_video(
            self._uploaded_dir(),
            self.config.video_extensions,
        )
        if recycled is None:
            return []
        return [(recycled, True)]

    def _uploaded_dir(self) -> Path:
        return (self.config.watch_folder / self.config.uploaded_subfolder).resolve()

    def _pending_new_videos(self) -> list[Path]:
        watch_folder = self.config.watch_folder
        if not watch_folder.exists():
            watch_folder.mkdir(parents=True, exist_ok=True)
            logger.warning("Created watch folder: %s", watch_folder)
            return []

        uploaded_dir = self._uploaded_dir()
        videos: list[Path] = []

        for path in watch_folder.iterdir():
            if not path.is_file():
                continue
            if path.suffix.lower() not in self.config.video_extensions:
                continue
            if self.state.is_uploaded(path):
                continue
            videos.append(path)

        videos.sort(key=lambda item: item.stat().st_mtime)
        return videos

    def _upload_video(self, video_path: Path, title: str, recycle: bool = False) -> str:
        description = self.config.upload.description_template
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": self.config.upload.tags,
                "categoryId": self.config.upload.category_id,
            },
            "status": {
                "privacyStatus": self.config.upload.privacy,
            },
        }

        mime_type = mimetypes.guess_type(video_path.name)[0] or "video/*"
        media = MediaFileUpload(
            str(video_path),
            mimetype=mime_type,
            chunksize=8 * 1024 * 1024,
            resumable=True,
        )

        logger.info("Uploading %s as '%s'%s", video_path.name, title, " (recycle)" if recycle else "")
        request = self.youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                logger.info("Upload progress for %s: %s%%", video_path.name, progress)

        del media
        del request

        video_id = response["id"]
        logger.info("Upload complete: https://youtu.be/%s", video_id)

        self._maybe_set_thumbnail(video_id, video_path)
        self.state.mark_uploaded(video_path, video_id, title)
        if not recycle:
            self._move_to_uploaded_folder(video_path)
        return video_id

    def _build_title(self, video_path: Path) -> str:
        title_file = video_path.with_suffix(".title.txt")
        if title_file.exists():
            title = title_file.read_text(encoding="utf-8").strip()
            if title:
                return title[:100]

        stem = video_path.stem
        if stem.count("_") >= 2:
            # Strip leading timestamp prefix: YYYYMMDD_HHMMSS_name
            parts = stem.split("_", 2)
            if len(parts[0]) == 8 and parts[0].isdigit() and len(parts[1]) == 6 and parts[1].isdigit():
                stem = parts[2]

        title = self.config.upload.title_template.replace("{filename}", stem)
        return title[:100]

    def _maybe_set_thumbnail(self, video_id: str, video_path: Path) -> None:
        thumbnail_path = self._resolve_thumbnail(video_path)
        if not thumbnail_path:
            return

        media = MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg", resumable=True)
        self.youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        logger.info("Thumbnail set from %s", thumbnail_path.name)

    def _resolve_thumbnail(self, video_path: Path) -> Path | None:
        configured = self.config.upload.thumbnail.strip()
        if configured:
            path = Path(configured)
            if path.exists():
                return path

        for suffix in (".jpg", ".jpeg", ".png", ".webp"):
            candidate = video_path.with_suffix(suffix)
            if candidate.exists():
                return candidate

        return None

    def _move_with_retry(self, source: Path, destination: Path, attempts: int = 8) -> None:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                shutil.move(str(source), str(destination))
                return
            except PermissionError as error:
                last_error = error
                time.sleep(0.5 * (attempt + 1))

        shutil.copy2(source, destination)
        for attempt in range(attempts):
            try:
                source.unlink()
                return
            except PermissionError as error:
                last_error = error
                time.sleep(0.5 * (attempt + 1))

        raise PermissionError(
            f"Upload succeeded but Windows still has the file locked: {source.name}. "
            f"Copied to {destination}. You can delete the original manually."
        ) from last_error

    def _move_to_uploaded_folder(self, video_path: Path) -> None:
        destination_dir = self.config.watch_folder / self.config.uploaded_subfolder
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / video_path.name

        if destination.exists():
            destination = destination_dir / f"{video_path.stem}_uploaded{video_path.suffix}"

        try:
            self._move_with_retry(video_path, destination)
            logger.info("Moved file to %s", destination)
        except PermissionError as error:
            logger.warning(str(error))
            return

        title_file = video_path.with_suffix(".title.txt")
        if title_file.exists():
            moved_title = destination.with_suffix(".title.txt")
            try:
                self._move_with_retry(title_file, moved_title)
            except PermissionError:
                logger.warning("Could not move title file: %s", title_file.name)

    def _acquire_lock(self) -> None:
        lock_path = self.config.paths.lock_file
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        if lock_path.exists():
            raise UploadLockError(
                f"Another upload is in progress (lock file: {lock_path}). "
                "Delete it only if no upload is running."
            )
        lock_path.write_text("locked", encoding="utf-8")

    def _release_lock(self) -> None:
        lock_path = self.config.paths.lock_file
        if lock_path.exists():
            lock_path.unlink()
