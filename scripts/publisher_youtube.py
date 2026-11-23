"""Upload to YouTube through the official Data API v3 (free quota).

Scheduled publishing uses the `publishAt` field: upload as private with a
future timestamp and YouTube flips it to public automatically at that time.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import Config

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]


def _credentials(cfg: Config):
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_id = cfg.env_get("YOUTUBE_CLIENT_ID")
    client_secret = cfg.env_get("YOUTUBE_CLIENT_SECRET")
    refresh_token = cfg.env_get("YOUTUBE_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        raise RuntimeError(
            "YouTube credentials missing. Need YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET and "
            "YOUTUBE_REFRESH_TOKEN. See README Step 4."
        )
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )


def youtube_service(cfg: Config):
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=_credentials(cfg), cache_discovery=False)


def upload_video(
    cfg: Config,
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    publish_at: datetime | None = None,
    privacy: str | None = None,
    thumbnail: Path | None = None,
) -> dict:
    service = youtube_service(cfg)

    snippet = {
        "title": title[:100],
        "description": description[:5000],
        "tags": [t[:30] for t in tags][:15],
        "categoryId": str(cfg.get("platforms", "youtube", "category_id", default="22")),
    }

    status: dict = {
        "selfDeclaredMadeForKids": bool(cfg.get("platforms", "youtube", "made_for_kids", default=False)),
    }
    if publish_at is not None:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at.astimezone().isoformat()
    else:
        status["privacyStatus"] = privacy or cfg.get("platforms", "youtube", "privacy_status", default="public")

    request = service.videos().insert(
        part="snippet,status",
        body={"snippet": snippet, "status": status},
        media_body=str(video_path),
    )
    response = request.execute()

    if thumbnail is not None and thumbnail.exists():
        try:
            service.thumbnails().set(
                videoId=response["id"],
                media_body=str(thumbnail),
            ).execute()
        except Exception:  # noqa: BLE001
            pass

    return {
        "video_id": response.get("id"),
        "title": response.get("snippet", {}).get("title", title),
        "status": response.get("status", {}).get("privacyStatus"),
        "publish_at": status.get("publishAt"),
        "url": f"https://youtu.be/{response.get('id')}",
    }


def set_visibility(cfg: Config, video_id: str, privacy: str) -> dict:
    service = youtube_service(cfg)
    return service.videos().update(part="status", body={"id": video_id, "status": {"privacyStatus": privacy}}).execute()


def list_uploads(cfg: Config, max_results: int = 20) -> list[dict]:
    service = youtube_service(cfg)
    response = service.videos().list(
        part="snippet,status",
        mine=True,
        maxResults=max_results,
        order="date",
    ).execute()
    return response.get("items", [])


def auth_setup_script(cfg: Config) -> str:
    return json.dumps({"scopes": SCOPES}, indent=2)
