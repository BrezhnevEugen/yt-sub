from __future__ import annotations

from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from storage import CLIENT_SECRET_PATH, TOKEN_PATH

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


class AuthError(Exception):
    pass


class YouTubeClient:
    def __init__(self) -> None:
        self._creds: Optional[Credentials] = self._load_creds()

    def _load_creds(self) -> Optional[Credentials]:
        if not TOKEN_PATH.exists():
            return None
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        except Exception:
            return None
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                TOKEN_PATH.write_text(creds.to_json())
            except Exception:
                return None
        return creds if creds and creds.valid else None

    def is_authenticated(self) -> bool:
        return self._creds is not None and self._creds.valid

    def sign_in(self) -> None:
        if not CLIENT_SECRET_PATH.exists():
            raise AuthError(f"client_secret.json not found at {CLIENT_SECRET_PATH}")
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True)
        TOKEN_PATH.write_text(creds.to_json())
        self._creds = creds

    def sign_out(self) -> None:
        if TOKEN_PATH.exists():
            TOKEN_PATH.unlink()
        self._creds = None

    def fetch_metadata(self, video_id: str) -> dict:
        if not self.is_authenticated():
            raise AuthError("Not signed in")
        youtube = build("youtube", "v3", credentials=self._creds, cache_discovery=False)
        resp = (
            youtube.videos()
            .list(
                part="snippet,contentDetails,statistics,status,topicDetails",
                id=video_id,
            )
            .execute()
        )
        items = resp.get("items", [])
        if not items:
            raise ValueError(f"Video not found: {video_id}")
        return items[0]
