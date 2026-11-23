"""Publish Reels to Instagram through the official Graph API (free).

Flow (official Instagram Content Publishing API):
  1. POST /{ig-user-id}/media          -> create the media container
  2. GET  /{container-id}?fields=...   -> wait until status_code is FINISHED
  3. POST /{ig-user-id}/media_publish  -> publish the container

Requirements:
  - Instagram Business or Creator account
  - A Facebook Page linked to that account
  - A Meta developer app with permissions:
      instagram_basic, instagram_content_publish, pages_read_engagement, pages_manage_posts
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

from .config import Config

GRAPH_VERSION = "v21.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"


def _token(cfg: Config) -> str:
    token = cfg.env_get("INSTAGRAM_ACCESS_TOKEN")
    if not token:
        raise RuntimeError(
            "INSTAGRAM_ACCESS_TOKEN missing. See README Step 5 for the free Meta app setup."
        )
    return token


def _ig_user_id(cfg: Config) -> str:
    user_id = cfg.env_get("INSTAGRAM_USER_ID")
    if not user_id:
        raise RuntimeError("INSTAGRAM_USER_ID missing. See README Step 5.")
    return user_id


def _post(url: str, data: dict, timeout: int = 120) -> dict:
    response = requests.post(url, data=data, timeout=timeout)
    if response.status_code >= 400:
        body = response.text[:600]
        raise RuntimeError(f"Graph API {response.status_code}: {body}")
    return response.json()


def _get(url: str, params: dict, timeout: int = 60) -> dict:
    response = requests.get(url, params=params, timeout=timeout)
    if response.status_code >= 400:
        raise RuntimeError(f"Graph API {response.status_code}: {response.text[:600]}")
    return response.json()


def create_container(cfg: Config, video_url: str, caption: str) -> str:
    payload = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": _token(cfg),
    }
    result = _post(f"{GRAPH}/{_ig_user_id(cfg)}/media", payload)
    container_id = result.get("id")
    if not container_id:
        raise RuntimeError(f"Container creation returned no id: {result}")
    return container_id


def wait_for_container(cfg: Config, container_id: str, timeout_seconds: int = 900) -> str:
    deadline = time.time() + timeout_seconds
    status = "UNKNOWN"
    while time.time() < deadline:
        result = _get(
            f"{GRAPH}/{container_id}",
            {"fields": "status_code,status", "access_token": _token(cfg)},
        )
        status = result.get("status_code", "UNKNOWN")
        if status == "FINISHED":
            return status
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram container failed: {status} {result.get('status')}")
        time.sleep(15)
    raise RuntimeError(f"Instagram container still {status} after {timeout_seconds}s")


def publish_container(cfg: Config, container_id: str) -> dict:
    payload = {
        "creation_id": container_id,
        "access_token": _token(cfg),
    }
    return _post(f"{GRAPH}/{_ig_user_id(cfg)}/media_publish", payload)


def publish_reel(cfg: Config, video_path: Path, video_url: str, caption: str) -> dict:
    """Full pipeline: container -> wait -> publish."""
    caption = caption[: int(cfg.get("platforms", "instagram", "caption_max_chars", default=2200))]
    container_id = create_container(cfg, video_url, caption)
    wait_for_container(cfg, container_id)
    result = publish_container(cfg, container_id)
    media_id = result.get("id")
    permalink = ""
    try:
        permalink = _get(
            f"{GRAPH}/{media_id}",
            {"fields": "permalink", "access_token": _token(cfg)},
        ).get("permalink", "")
    except Exception:  # noqa: BLE001
        pass
    return {
        "media_id": media_id,
        "container_id": container_id,
        "permalink": permalink,
        "caption": caption,
        "video_url": video_url,
    }


def account_info(cfg: Config) -> dict:
    return _get(
        f"{GRAPH}/{_ig_user_id(cfg)}",
        {"fields": "username,id,name", "access_token": _token(cfg)},
    )
