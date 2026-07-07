from __future__ import annotations

import json
import logging
import secrets
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event

import requests

logger = logging.getLogger(__name__)

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

DEFAULT_SCOPES_INBOX = "user.info.basic,video.upload"
DEFAULT_SCOPES_DIRECT = "user.info.basic,video.publish"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"


@dataclass
class TikTokAppCredentials:
    client_key: str
    client_secret: str
    redirect_uri: str = DEFAULT_REDIRECT_URI


@dataclass
class TikTokTokenData:
    access_token: str
    refresh_token: str
    open_id: str
    scope: str
    expires_at: float

    @classmethod
    def from_api_response(cls, payload: dict) -> TikTokTokenData:
        expires_in = int(payload.get("expires_in", 86400))
        return cls(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload.get("refresh_token", "")),
            open_id=str(payload.get("open_id", "")),
            scope=str(payload.get("scope", "")),
            expires_at=time.time() + max(expires_in - 300, 60),
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "open_id": self.open_id,
                "scope": self.scope,
                "expires_at": self.expires_at,
            },
            indent=2,
        )

    @classmethod
    def from_file(cls, path: Path) -> TikTokTokenData:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            access_token=str(data["access_token"]),
            refresh_token=str(data.get("refresh_token", "")),
            open_id=str(data.get("open_id", "")),
            scope=str(data.get("scope", "")),
            expires_at=float(data.get("expires_at", 0)),
        )


def load_app_credentials(app_path: Path, config_key: str, config_secret: str, redirect_uri: str) -> TikTokAppCredentials:
    if app_path.exists():
        data = json.loads(app_path.read_text(encoding="utf-8"))
        return TikTokAppCredentials(
            client_key=str(data["client_key"]).strip(),
            client_secret=str(data["client_secret"]).strip(),
            redirect_uri=str(data.get("redirect_uri", redirect_uri)).strip(),
        )

    if config_key and config_secret:
        return TikTokAppCredentials(
            client_key=config_key,
            client_secret=config_secret,
            redirect_uri=redirect_uri or DEFAULT_REDIRECT_URI,
        )

    raise FileNotFoundError(
        f"Missing TikTok app credentials at {app_path}.\n"
        "Copy credentials/tiktok_app.json.example and fill in client_key + client_secret "
        "from https://developers.tiktok.com/"
    )


def _exchange_code(app: TikTokAppCredentials, code: str) -> TikTokTokenData:
    response = requests.post(
        TIKTOK_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": app.client_key,
            "client_secret": app.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": app.redirect_uri,
        },
        timeout=60,
    )
    payload = response.json()
    if response.status_code >= 400 or "access_token" not in payload:
        error = payload.get("error_description") or payload.get("error") or response.text
        raise RuntimeError(f"TikTok token exchange failed: {error}")
    return TikTokTokenData.from_api_response(payload)


def _refresh_token(app: TikTokAppCredentials, token: TikTokTokenData) -> TikTokTokenData:
    if not token.refresh_token:
        raise RuntimeError("TikTok refresh token missing. Run: python main.py tiktok-auth")

    response = requests.post(
        TIKTOK_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": app.client_key,
            "client_secret": app.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
        },
        timeout=60,
    )
    payload = response.json()
    if response.status_code >= 400 or "access_token" not in payload:
        error = payload.get("error_description") or payload.get("error") or response.text
        raise RuntimeError(f"TikTok token refresh failed: {error}")
    return TikTokTokenData.from_api_response(payload)


def run_tiktok_oauth(
    app_path: Path,
    token_path: Path,
    config_key: str = "",
    config_secret: str = "",
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    post_mode: str = "inbox",
    force: bool = False,
) -> TikTokTokenData:
    if force and token_path.exists():
        token_path.unlink()
        logger.info("Removed old TikTok token at %s", token_path)

    app = load_app_credentials(app_path, config_key, config_secret, redirect_uri)
    scopes = DEFAULT_SCOPES_DIRECT if post_mode == "direct" else DEFAULT_SCOPES_INBOX
    state = secrets.token_urlsafe(16)
    code_holder: dict[str, str] = {}
    done = Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != urllib.parse.urlparse(app.redirect_uri).path:
                self.send_response(404)
                self.end_headers()
                return

            params = urllib.parse.parse_qs(parsed.query)
            if params.get("state", [""])[0] != state:
                self._respond(400, "State mismatch. Close this tab and try again.")
                return

            if "error" in params:
                message = params.get("error_description", params["error"])[0]
                self._respond(400, f"TikTok authorization failed: {message}")
                done.set()
                return

            code = params.get("code", [""])[0]
            if not code:
                self._respond(400, "Missing authorization code.")
                done.set()
                return

            code_holder["code"] = code
            self._respond(200, "TikTok connected. You can close this tab and return to the terminal.")
            done.set()

        def _respond(self, status: int, message: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                f"<html><body><h2>{message}</h2></body></html>".encode("utf-8")
            )

        def log_message(self, format: str, *args) -> None:
            return

    parsed_redirect = urllib.parse.urlparse(app.redirect_uri)
    port = parsed_redirect.port or 8765

    auth_params = urllib.parse.urlencode(
        {
            "client_key": app.client_key,
            "scope": scopes,
            "response_type": "code",
            "redirect_uri": app.redirect_uri,
            "state": state,
        }
    )
    auth_url = f"{TIKTOK_AUTH_URL}?{auth_params}"

    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    server.timeout = 1

    logger.info("Opening TikTok login in your browser...")
    logger.info("If it does not open, visit:\n%s", auth_url)
    webbrowser.open(auth_url)

    while not done.is_set():
        server.handle_request()

    server.server_close()

    if "code" not in code_holder:
        raise RuntimeError("TikTok authorization did not complete.")

    token = _exchange_code(app, code_holder["code"])
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token.to_json(), encoding="utf-8")
    logger.info("TikTok auth OK. Token saved to %s (scopes: %s)", token_path, token.scope)
    return token


def get_tiktok_access_token(
    app_path: Path,
    token_path: Path,
    config_key: str = "",
    config_secret: str = "",
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    manual_token: str = "",
    post_mode: str = "inbox",
) -> str:
    if manual_token:
        return manual_token

    if not token_path.exists():
        raise RuntimeError(
            "TikTok not authenticated. Run: python main.py tiktok-auth"
        )

    app = load_app_credentials(app_path, config_key, config_secret, redirect_uri)
    token = TikTokTokenData.from_file(token_path)

    if time.time() < token.expires_at:
        return token.access_token

    refreshed = _refresh_token(app, token)
    token_path.write_text(refreshed.to_json(), encoding="utf-8")
    logger.info("Refreshed TikTok access token")
    return refreshed.access_token
