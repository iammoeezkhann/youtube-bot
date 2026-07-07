from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

from bot.config import MetaPlatformConfig
from bot.platforms.base import PlatformUploader, UploadResult

logger = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v21.0"


class InstagramPlatformUploader(PlatformUploader):
    name = "instagram"

    def __init__(self, config: MetaPlatformConfig) -> None:
        self.config = config

    def is_configured(self) -> bool:
        return bool(self.config.access_token and self.config.instagram_account_id)

    def upload(self, video_path: Path, title: str, description: str) -> UploadResult:
        if not self.is_configured():
            raise RuntimeError(
                "Instagram not configured. Set platforms.instagram in config.yaml "
                "(access_token + instagram_account_id from Meta Graph API)."
            )

        ig_id = self.config.instagram_account_id
        token = self.config.access_token
        caption = f"{title}\n\n{description}".strip()[:2200]

        init = requests.post(
            f"{GRAPH}/{ig_id}/media",
            data={
                "media_type": "REELS",
                "upload_type": "resumable",
                "caption": caption,
                "access_token": token,
            },
            timeout=60,
        )
        init.raise_for_status()
        container_id = init.json()["id"]
        upload_url = init.json().get("uri") or init.headers.get("Location")
        if not upload_url:
            raise RuntimeError("Instagram resumable upload URL missing from API response")

        video_bytes = video_path.read_bytes()
        upload = requests.post(
            upload_url,
            headers={
                "Authorization": f"OAuth {token}",
                "offset": "0",
                "file_size": str(len(video_bytes)),
            },
            data=video_bytes,
            timeout=600,
        )
        upload.raise_for_status()

        publish = requests.post(
            f"{GRAPH}/{ig_id}/media_publish",
            data={"creation_id": container_id, "access_token": token},
            timeout=60,
        )
        publish.raise_for_status()
        media_id = publish.json().get("id", container_id)

        return UploadResult(
            platform=self.name,
            post_id=str(media_id),
            url=f"https://www.instagram.com/reel/{media_id}/",
        )


class FacebookPlatformUploader(PlatformUploader):
    name = "facebook"

    def __init__(self, config: MetaPlatformConfig) -> None:
        self.config = config

    def is_configured(self) -> bool:
        return bool(self.config.access_token and self.config.facebook_page_id)

    def upload(self, video_path: Path, title: str, description: str) -> UploadResult:
        if not self.is_configured():
            raise RuntimeError(
                "Facebook not configured. Set platforms.facebook in config.yaml "
                "(access_token + facebook_page_id from Meta Graph API)."
            )

        page_id = self.config.facebook_page_id
        token = self.config.access_token

        with video_path.open("rb") as handle:
            response = requests.post(
                f"{GRAPH}/{page_id}/videos",
                data={
                    "title": title[:100],
                    "description": description[:5000],
                    "access_token": token,
                },
                files={"source": (video_path.name, handle, "video/mp4")},
                timeout=600,
            )
        response.raise_for_status()
        video_id = response.json().get("id", "")
        if not video_id:
            raise RuntimeError(f"Facebook upload returned no id: {response.text[:200]}")

        # Processing can take a moment on Meta's side
        time.sleep(2)
        return UploadResult(
            platform=self.name,
            post_id=str(video_id),
            url=f"https://www.facebook.com/{page_id}/videos/{video_id}",
        )
