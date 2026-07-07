from __future__ import annotations

import logging

from bot.auth import get_youtube_service
from bot.config import AppConfig
from bot.platforms.base import PlatformUploader
from bot.platforms.meta import FacebookPlatformUploader, InstagramPlatformUploader
from bot.platforms.tiktok import TikTokPlatformUploader
from bot.platforms.youtube import YouTubePlatformUploader

logger = logging.getLogger(__name__)


def build_platform_uploaders(config: AppConfig) -> list[PlatformUploader]:
    uploaders: list[PlatformUploader] = []

    if config.platforms.youtube.enabled:
        try:
            youtube = get_youtube_service(
                config.paths.client_secret, config.paths.token
            )
            uploaders.append(YouTubePlatformUploader(youtube, config.upload))
        except Exception as error:
            logger.error("YouTube uploader unavailable: %s", error)

    if config.platforms.instagram.enabled:
        uploaders.append(InstagramPlatformUploader(config.platforms.meta))

    if config.platforms.facebook.enabled:
        uploaders.append(FacebookPlatformUploader(config.platforms.meta))

    if config.platforms.tiktok.enabled:
        uploaders.append(
            TikTokPlatformUploader(
                config.platforms.tiktok,
                config.paths.tiktok_app,
                config.paths.tiktok_token,
            )
        )

    configured = [u for u in uploaders if u.is_configured()]
    names = ", ".join(u.name for u in configured) or "none"
    logger.info("Active upload platforms: %s", names)
    return configured
