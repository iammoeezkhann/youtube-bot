from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from bot.config import UploadConfig
from bot.platforms.base import PlatformUploader, UploadResult

logger = logging.getLogger(__name__)


class YouTubePlatformUploader(PlatformUploader):
    name = "youtube"

    def __init__(self, youtube_service, upload_config: UploadConfig) -> None:
        self.youtube = youtube_service
        self.upload_config = upload_config

    def is_configured(self) -> bool:
        return self.youtube is not None

    def upload(self, video_path: Path, title: str, description: str) -> UploadResult:
        body = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": self.upload_config.tags,
                "categoryId": self.upload_config.category_id,
            },
            "status": {"privacyStatus": self.upload_config.privacy},
        }

        mime_type = mimetypes.guess_type(video_path.name)[0] or "video/*"
        media = MediaFileUpload(
            str(video_path),
            mimetype=mime_type,
            chunksize=8 * 1024 * 1024,
            resumable=True,
        )

        request = self.youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(
                    "YouTube upload %s: %s%%",
                    video_path.name,
                    int(status.progress() * 100),
                )

        video_id = response["id"]
        return UploadResult(
            platform=self.name,
            post_id=video_id,
            url=f"https://youtu.be/{video_id}",
        )
