from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

from bot.config import TikTokPlatformConfig
from bot.platforms.base import PlatformUploader, UploadResult
from bot.tiktok_auth import get_tiktok_access_token

logger = logging.getLogger(__name__)

TIKTOK_API = "https://open.tiktokapis.com/v2"


class TikTokPlatformUploader(PlatformUploader):
    name = "tiktok"

    def __init__(self, config: TikTokPlatformConfig, app_path: Path, token_path: Path) -> None:
        self.config = config
        self.app_path = app_path
        self.token_path = token_path

    def is_configured(self) -> bool:
        if not self.config.enabled:
            return False
        if self.config.access_token:
            return True
        return self.token_path.exists() or (
            bool(self.config.client_key) and bool(self.config.client_secret)
        )

    def _token(self) -> str:
        return get_tiktok_access_token(
            self.app_path,
            self.token_path,
            config_key=self.config.client_key,
            config_secret=self.config.client_secret,
            redirect_uri=self.config.redirect_uri,
            manual_token=self.config.access_token,
            post_mode=self.config.post_mode,
        )

    def upload(self, video_path: Path, title: str, description: str) -> UploadResult:
        if not self.is_configured():
            raise RuntimeError(
                "TikTok not configured. Run: python main.py tiktok-auth"
            )

        token = self._token()
        size = video_path.stat().st_size
        caption = f"{title}\n{description}".strip()[:2200]

        if self.config.post_mode == "direct":
            init_url = f"{TIKTOK_API}/post/publish/video/init/"
            body = {
                "post_info": {
                    "title": title[:150],
                    "privacy_level": self.config.privacy_level,
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                    "video_cover_timestamp_ms": 1000,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": size,
                    "total_chunk_count": 1,
                },
            }
            if caption:
                body["post_info"]["description"] = caption[:2200]
        else:
            init_url = f"{TIKTOK_API}/post/publish/inbox/video/init/"
            body = {
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": size,
                    "total_chunk_count": 1,
                }
            }

        init = requests.post(
            init_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json=body,
            timeout=60,
        )
        if init.status_code >= 400:
            raise RuntimeError(self._format_error("init", init))

        payload = init.json().get("data", {})
        upload_url = payload.get("upload_url")
        publish_id = str(payload.get("publish_id", ""))
        if not upload_url:
            raise RuntimeError(f"TikTok init failed: {init.text[:300]}")

        with video_path.open("rb") as handle:
            put = requests.put(
                upload_url,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(size),
                },
                data=handle,
                timeout=600,
            )
        if put.status_code >= 400:
            raise RuntimeError(self._format_error("upload", put))

        status = self._wait_for_publish(token, publish_id)
        if status == "FAILED":
            raise RuntimeError("TikTok processing failed. Check logs/bot.log for details.")

        if self.config.post_mode == "inbox":
            url = "https://www.tiktok.com/"
            logger.info(
                "TikTok inbox upload sent (publish_id=%s). "
                "Open TikTok app → inbox notification to finish posting.",
                publish_id,
            )
        else:
            url = "https://www.tiktok.com/"
            logger.info("TikTok direct post complete (publish_id=%s)", publish_id)

        return UploadResult(
            platform=self.name,
            post_id=publish_id,
            url=url,
        )

    def _wait_for_publish(self, token: str, publish_id: str, timeout_sec: int = 120) -> str:
        if not publish_id:
            return "UNKNOWN"

        deadline = time.time() + timeout_sec
        last_status = "PROCESSING"

        while time.time() < deadline:
            response = requests.post(
                f"{TIKTOK_API}/post/publish/status/fetch/",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                json={"publish_id": publish_id},
                timeout=60,
            )
            if response.status_code >= 400:
                logger.warning("TikTok status check failed: %s", response.text[:200])
                time.sleep(3)
                continue

            data = response.json().get("data", {})
            last_status = str(data.get("status", last_status))
            logger.info("TikTok publish status: %s", last_status)

            if last_status in {"PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"}:
                return last_status
            if last_status == "FAILED":
                reason = data.get("fail_reason", "unknown")
                raise RuntimeError(f"TikTok publish failed: {reason}")
            time.sleep(3)

        logger.warning("TikTok status timed out at %s", last_status)
        return last_status

    @staticmethod
    def _format_error(stage: str, response: requests.Response) -> str:
        try:
            payload = response.json()
            error = payload.get("error", {})
            if isinstance(error, dict):
                code = error.get("code", "")
                message = error.get("message", "")
                return f"TikTok {stage} error ({response.status_code}): {code} {message}".strip()
        except Exception:
            pass
        return f"TikTok {stage} error ({response.status_code}): {response.text[:300]}"
