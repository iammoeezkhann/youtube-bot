from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from bot.channel_sync import pick_random_video
from bot.config import AppConfig
from bot.platforms.base import PlatformUploader
from bot.state import UploadState
from bot.telegram_notify import (
    notify_upload_complete_multi,
    notify_upload_failed_multi,
    notify_upload_started_multi,
)

logger = logging.getLogger(__name__)


class UploadLockError(RuntimeError):
    pass


class MultiPlatformUploader:
    def __init__(
        self,
        config: AppConfig,
        platforms: list[PlatformUploader],
        state: UploadState,
    ) -> None:
        self.config = config
        self.platforms = platforms
        self.state = state

    def run_once(self) -> int:
        if not self.platforms:
            raise RuntimeError(
                "No upload platforms configured. Enable at least one in config.yaml."
            )

        self._acquire_lock()
        try:
            picks = self._pick_videos()
            if not picks:
                logger.info("No videos available to upload")
                return 0

            uploaded_runs = 0
            for video_path, is_recycle in picks:
                title = self._build_title(video_path)
                try:
                    if is_recycle:
                        logger.info("Recycling: %s", video_path.name)
                    notify_upload_started_multi(title, video_path.name, self.platforms)
                    results = self._upload_all_platforms(video_path, title, is_recycle)
                    notify_upload_complete_multi(title, results)
                    uploaded_runs += 1
                except Exception as error:
                    logger.exception("Upload failed for %s", video_path.name)
                    notify_upload_failed_multi(title, video_path.name, str(error))
            return uploaded_runs
        finally:
            self._release_lock()

    def _upload_all_platforms(
        self, video_path: Path, title: str, recycle: bool
    ) -> dict[str, str]:
        description = self.config.upload.description_template
        results: dict[str, str] = {}

        for platform in self.platforms:
            if self.state.is_uploaded_to_platform(video_path, platform.name) and not recycle:
                logger.info(
                    "Skipping %s on %s (already uploaded)", video_path.name, platform.name
                )
                continue
            try:
                result = platform.upload(video_path, title, description)
                results[platform.name] = result.url
                self.state.mark_platform_upload(
                    video_path, platform.name, result.post_id, title
                )
                logger.info(
                    "%s upload OK: %s", platform.name, result.url
                )
            except Exception as error:
                logger.exception("%s upload failed: %s", platform.name, error)
                results[platform.name] = f"FAILED: {error}"

        if not recycle and video_path.parent.resolve() == self.config.watch_folder.resolve():
            self._move_to_uploaded_folder(video_path)

        return results

    def _pick_videos(self) -> list[tuple[Path, bool]]:
        pending = self._pending_new_videos()
        if pending:
            if self.config.upload.mode == "one":
                pending = pending[:1]
            return [(path, False) for path in pending]

        if self.config.upload.pick_strategy == "random":
            video = pick_random_video(
                self.config.sources_folder,
                self.config.watch_folder,
                self.config.video_extensions,
            )
            if video:
                is_recycle = (
                    video.parent.resolve() != self.config.watch_folder.resolve()
                )
                return [(video, is_recycle)]

        if not self.config.upload.recycle_uploads:
            return []

        recycled = self.state.next_recycle_video(
            self._uploaded_dir(),
            self.config.video_extensions,
        )
        if recycled:
            return [(recycled, True)]

        video = pick_random_video(
            self.config.sources_folder,
            self.config.watch_folder,
            self.config.video_extensions,
        )
        if video:
            is_recycle = video.parent.resolve() != self.config.watch_folder.resolve()
            return [(video, is_recycle)]
        return []

    def _pending_new_videos(self) -> list[Path]:
        watch_folder = self.config.watch_folder
        if not watch_folder.exists():
            watch_folder.mkdir(parents=True, exist_ok=True)
            return []

        videos = [
            path
            for path in watch_folder.iterdir()
            if path.is_file()
            and path.suffix.lower() in self.config.video_extensions
            and not self.state.is_fully_uploaded(path, [p.name for p in self.platforms])
        ]
        videos.sort(key=lambda item: item.stat().st_mtime)
        return videos

    def _uploaded_dir(self) -> Path:
        return (self.config.watch_folder / self.config.uploaded_subfolder).resolve()

    def _build_title(self, video_path: Path) -> str:
        title_file = video_path.with_suffix(".title.txt")
        if title_file.exists():
            title = title_file.read_text(encoding="utf-8").strip()
            if title:
                return title[:100]

        stem = video_path.stem
        if stem.count("_") >= 2:
            parts = stem.split("_", 2)
            if len(parts[0]) == 8 and parts[0].isdigit() and len(parts[1]) == 6 and parts[1].isdigit():
                stem = parts[2]

        return self.config.upload.title_template.replace("{filename}", stem)[:100]

    def _move_with_retry(self, source: Path, destination: Path, attempts: int = 8) -> None:
        for attempt in range(attempts):
            try:
                shutil.move(str(source), str(destination))
                return
            except PermissionError:
                time.sleep(0.5 * (attempt + 1))
        shutil.copy2(source, destination)
        source.unlink(missing_ok=True)

    def _move_to_uploaded_folder(self, video_path: Path) -> None:
        destination_dir = self._uploaded_dir()
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / video_path.name
        if destination.exists():
            destination = destination_dir / f"{video_path.stem}_uploaded{video_path.suffix}"
        try:
            self._move_with_retry(video_path, destination)
        except Exception as error:
            logger.warning("Could not move %s: %s", video_path.name, error)

    def _acquire_lock(self) -> None:
        lock_path = self.config.paths.lock_file
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        if lock_path.exists():
            raise UploadLockError(
                f"Another upload is in progress (lock: {lock_path}). "
                "Delete only if no upload is running."
            )
        lock_path.write_text("locked", encoding="utf-8")

    def _release_lock(self) -> None:
        lock_path = self.config.paths.lock_file
        if lock_path.exists():
            lock_path.unlink()
