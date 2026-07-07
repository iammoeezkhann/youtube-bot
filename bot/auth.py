from __future__ import annotations

import logging
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_youtube_service(client_secret: Path, token_path: Path):
    credentials = _load_credentials(client_secret, token_path)
    return build("youtube", "v3", credentials=credentials)


def _load_credentials(client_secret: Path, token_path: Path) -> Credentials:
    credentials = None
    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if credentials and credentials.valid:
        return credentials

    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(credentials.to_json(), encoding="utf-8")
            return credentials
        except RefreshError as error:
            logger.warning(
                "OAuth token expired or revoked (%s). Re-authentication required.",
                error,
            )
            token_path.unlink(missing_ok=True)
            credentials = None

    if not client_secret.exists():
        raise FileNotFoundError(
            f"Missing OAuth client secret at {client_secret}. "
            "Download it from Google Cloud Console and save it there."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
    credentials = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    return credentials
