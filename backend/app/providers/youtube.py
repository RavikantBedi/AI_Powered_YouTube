"""YouTube Data API v3 adapter: OAuth 2.0 installed-app flow, resumable upload,
thumbnail set, and caption tracks.

The user connects once (POST /api/youtube/connect → open the returned URL → paste the
code back). The refresh token is stored at YOUTUBE_TOKEN_FILE with owner-only
permissions and reused for every subsequent publish.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from pathlib import Path

from app.config import get_settings
from app.core.errors import ProviderUnavailableError
from app.providers.base import YouTubeProvider

log = logging.getLogger("agency.youtube")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def _require_google_libs():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import Flow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as e:
        raise ProviderUnavailableError(
            "youtube", "google client libraries not installed",
            missing=["google-api-python-client", "google-auth-oauthlib"]) from e
    return Request, Credentials, Flow, build, MediaFileUpload


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # best-effort on Windows
        pass


class YouTubeOAuth:
    """Out-of-band OAuth helper used by the /api/youtube/* endpoints."""

    def __init__(self) -> None:
        s = get_settings()
        self.client_secret_file = Path(s.youtube_client_secret_file)
        self.token_file = Path(s.youtube_token_file)

    def is_connected(self) -> bool:
        return self.token_file.exists()

    def start_flow(self, redirect_uri: str) -> tuple[str, str]:
        _, _, Flow, _, _ = _require_google_libs()
        if not self.client_secret_file.exists():
            raise ProviderUnavailableError(
                "youtube", f"OAuth client secret not found at {self.client_secret_file}",
                missing=["YOUTUBE_CLIENT_SECRET_FILE"])
        flow = Flow.from_client_secrets_file(
            str(self.client_secret_file), scopes=SCOPES, redirect_uri=redirect_uri)
        auth_url, state = flow.authorization_url(
            access_type="offline", prompt="consent", include_granted_scopes="true")
        return auth_url, state

    def finish_flow(self, code: str, redirect_uri: str) -> dict:
        _, _, Flow, build, _ = _require_google_libs()
        flow = Flow.from_client_secrets_file(
            str(self.client_secret_file), scopes=SCOPES, redirect_uri=redirect_uri)
        flow.fetch_token(code=code)
        creds = flow.credentials
        _write_private(self.token_file, creds.to_json())
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
        resp = youtube.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items", [])
        return {
            "connected": True,
            "channel_id": items[0]["id"] if items else "",
            "channel_title": items[0]["snippet"]["title"] if items else "",
        }

    def credentials(self):
        Request, Credentials, _, _, _ = _require_google_libs()
        if not self.token_file.exists():
            raise ProviderUnavailableError(
                "youtube", "YouTube account not connected — POST /api/youtube/connect first",
                missing=["youtube_oauth_token"])
        creds = Credentials.from_authorized_user_info(
            json.loads(self.token_file.read_text(encoding="utf-8")), SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                _write_private(self.token_file, creds.to_json())
            else:
                raise ProviderUnavailableError(
                    "youtube", "stored credentials are invalid; reconnect the account",
                    missing=["youtube_oauth_token"])
        return creds


class YouTubeDataAPI(YouTubeProvider):
    name = "youtube"

    def __init__(self) -> None:
        self.oauth = YouTubeOAuth()
        # Report unavailable at construction so the capability report and the planner
        # see the truth: an unconnected account is not a usable provider.
        if not self.oauth.client_secret_file.exists():
            raise ProviderUnavailableError(
                "youtube", f"OAuth client secret not found at {self.oauth.client_secret_file}",
                missing=["YOUTUBE_CLIENT_SECRET_FILE"])
        if not self.oauth.is_connected():
            raise ProviderUnavailableError(
                "youtube", "no YouTube account connected — POST /api/youtube/connect",
                missing=["youtube_oauth_token"])

    async def upload(self, *, video_path: Path, title: str, description: str, tags: list[str],
                     category_id: str, language: str, privacy_status: str,
                     thumbnail_path: Path | None = None,
                     captions: list[tuple[Path, str]] | None = None) -> dict:
        # googleapiclient is synchronous — run the whole publish in a worker thread.
        return await asyncio.to_thread(
            self._upload_sync, video_path, title, description, tags, category_id,
            language, privacy_status, thumbnail_path, captions or [])

    def _upload_sync(self, video_path: Path, title: str, description: str, tags: list[str],
                     category_id: str, language: str, privacy_status: str,
                     thumbnail_path: Path | None, captions: list[tuple[Path, str]]) -> dict:
        _, _, _, build, MediaFileUpload = _require_google_libs()
        from googleapiclient.errors import HttpError

        creds = self.oauth.credentials()
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags[:critical_tag_limit()],
                "categoryId": category_id,
                "defaultLanguage": language,
                "defaultAudioLanguage": language,
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }
        media = MediaFileUpload(str(video_path), chunksize=8 * 1024 * 1024, resumable=True,
                                mimetype="video/mp4")
        try:
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    log.info("youtube upload %d%%", int(status.progress() * 100))
        except HttpError as e:
            raise ProviderUnavailableError("youtube", f"upload failed: {e}") from e

        video_id = response["id"]
        result = {
            "video_id": video_id,
            "video_url": f"https://www.youtube.com/watch?v={video_id}",
            "privacy_status": privacy_status,
            "thumbnail_status": "skipped",
            "caption_status": "skipped",
        }

        if thumbnail_path and Path(thumbnail_path).exists():
            try:
                youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/png"),
                ).execute()
                result["thumbnail_status"] = "uploaded"
            except HttpError as e:
                # Custom thumbnails need a verified channel; don't fail the publish.
                log.warning("thumbnail upload failed: %s", e)
                result["thumbnail_status"] = f"failed: {e.status_code if hasattr(e, 'status_code') else e}"

        uploaded_captions = []
        for path, lang in captions:
            if not Path(path).exists():
                continue
            try:
                youtube.captions().insert(
                    part="snippet",
                    body={"snippet": {"videoId": video_id, "language": lang,
                                      "name": f"{lang} subtitles", "isDraft": False}},
                    media_body=MediaFileUpload(str(path), mimetype="application/octet-stream"),
                ).execute()
                uploaded_captions.append(lang)
            except HttpError as e:
                log.warning("caption upload failed for %s: %s", lang, e)
        if uploaded_captions:
            result["caption_status"] = "uploaded: " + ",".join(uploaded_captions)

        return result

    async def verify(self, video_id: str) -> dict:
        return await asyncio.to_thread(self._verify_sync, video_id)

    def _verify_sync(self, video_id: str) -> dict:
        _, _, _, build, _ = _require_google_libs()
        youtube = build("youtube", "v3", credentials=self.oauth.credentials(),
                        cache_discovery=False)
        resp = youtube.videos().list(part="snippet,status,processingDetails",
                                     id=video_id).execute()
        items = resp.get("items", [])
        if not items:
            return {"found": False}
        item = items[0]
        return {
            "found": True,
            "title": item["snippet"]["title"],
            "privacy_status": item["status"]["privacyStatus"],
            "upload_status": item["status"].get("uploadStatus", ""),
            "processing_status": item.get("processingDetails", {}).get("processingStatus", ""),
        }


def critical_tag_limit() -> int:
    """YouTube caps the tags field at 500 characters total; 30 tags is the practical limit."""
    return 30
