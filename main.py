from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from bot.auth import get_youtube_service
from bot.config import load_config
from bot.multi_uploader import MultiPlatformUploader, UploadLockError
from bot.platforms.registry import build_platform_uploaders
from bot.scheduler import start_scheduler
from bot.state import UploadState
from bot.telegram_bot import start_telegram_polling
from bot.tiktok_auth import run_tiktok_oauth

DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.yaml"


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def build_uploader(config_path: Path) -> MultiPlatformUploader:
    config = load_config(config_path)
    setup_logging(config.paths.log_file)
    platforms = build_platform_uploaders(config)
    state = UploadState(config.paths.state_file)
    return MultiPlatformUploader(config, platforms, state)


def cmd_auth(config_path: Path, force: bool = False) -> int:
    config = load_config(config_path)
    setup_logging(config.paths.log_file)

    if force and config.paths.token.exists():
        config.paths.token.unlink()
        logging.info("Removed old token at %s", config.paths.token)

    if config.platforms.youtube.enabled:
        get_youtube_service(config.paths.client_secret, config.paths.token)
        logging.info("YouTube auth OK. Token saved to %s", config.paths.token)
    else:
        logging.info("YouTube is disabled in config — skipping OAuth.")

    logging.info(
        "Platform setup:\n"
        "  YouTube: python main.py auth\n"
        "  TikTok: python main.py tiktok-auth\n"
        "  Instagram/Facebook: Meta Graph API token in config.yaml"
    )
    return 0


def cmd_tiktok_auth(config_path: Path, force: bool = False) -> int:
    config = load_config(config_path)
    setup_logging(config.paths.log_file)

    run_tiktok_oauth(
        app_path=config.paths.tiktok_app,
        token_path=config.paths.tiktok_token,
        config_key=config.platforms.tiktok.client_key,
        config_secret=config.platforms.tiktok.client_secret,
        redirect_uri=config.platforms.tiktok.redirect_uri,
        post_mode=config.platforms.tiktok.post_mode,
        force=force,
    )

    mode = config.platforms.tiktok.post_mode
    if mode == "inbox":
        logging.info(
            "TikTok inbox mode: after each upload, open the TikTok app and tap the "
            "inbox notification to add caption and publish."
        )
    else:
        logging.info(
            "TikTok direct mode: videos post straight to your profile. "
            "Unaudited apps may be limited to private posts until TikTok approves your app."
        )
    return 0


def cmd_once(config_path: Path) -> int:
    uploader = build_uploader(config_path)
    try:
        count = uploader.run_once()
    except UploadLockError as error:
        logging.error(str(error))
        return 1
    logging.info("Finished. Uploaded %s video(s).", count)
    return 0


def cmd_telegram(config_path: Path) -> int:
    config = load_config(config_path)
    setup_logging(config.paths.log_file)
    start_telegram_polling(config)
    return 0


def _start_telegram_thread(config_path: Path) -> None:
    config = load_config(config_path)
    if not config.telegram.enabled:
        return

    def runner() -> None:
        while True:
            try:
                start_telegram_polling(config)
                return
            except Exception:
                logging.exception("Telegram bot crashed. Retrying in 30 seconds...")
                time.sleep(30)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    logging.info("Telegram listener running in background")


def cmd_run(config_path: Path) -> int:
    uploader = build_uploader(config_path)
    config = load_config(config_path)

    def job() -> None:
        try:
            count = uploader.run_once()
            logging.info("Scheduled run finished. Uploaded %s video(s).", count)
        except UploadLockError as error:
            logging.error(str(error))
        except Exception:
            logging.exception("Scheduled run failed")

    if config.telegram.enabled:
        _start_telegram_thread(config_path)

    if config.schedule.type == "daily_times":
        logging.info(
            "Waiting for scheduled upload times (%s)...",
            ", ".join(config.schedule.times),
        )
    else:
        logging.info("Running first upload immediately...")
        job()
    start_scheduler(config, job)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload videos to YouTube, TikTok, Instagram, and Facebook."
    )
    parser.add_argument(
        "command",
        choices=["auth", "tiktok-auth", "once", "run", "telegram"],
        help="auth = YouTube sign-in, tiktok-auth = TikTok sign-in, once = upload now, run = scheduler",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Path to config.yaml",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete saved OAuth token and sign in again (auth command only)",
    )
    args = parser.parse_args()

    if args.command == "auth":
        return cmd_auth(args.config, force=args.force)
    if args.command == "tiktok-auth":
        return cmd_tiktok_auth(args.config, force=args.force)
    if args.command == "once":
        return cmd_once(args.config)
    if args.command == "telegram":
        return cmd_telegram(args.config)
    return cmd_run(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
