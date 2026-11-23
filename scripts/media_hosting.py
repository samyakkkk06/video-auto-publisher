"""Give Instagram a public https URL for each clip.

Instagram's Graph API downloads the video itself, so the file has to be
reachable from the internet without any login. Free options implemented:

  github_release : upload the clip to a GitHub Release (free, public URL)
  raw_github     : the clip lives in a public GitHub repo (raw URL)
  external       : you already have a CDN / bucket, put its base URL in config
"""
from __future__ import annotations

import mimetypes
import os
import shutil
import subprocess
from pathlib import Path

import requests

from .config import Config

API = "https://api.github.com"
UPLOAD = "https://uploads.github.com"


def github_token(cfg: Config) -> str:
    """Token for the GitHub API.

    Order: GITHUB_TOKEN in .env, then the token from the GitHub CLI login.
    The CLI token already has repo and workflow scopes, so a separate
    personal access token is not needed on the machine you work from.
    """
    token = cfg.env_get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token

    if shutil.which("gh"):
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:  # noqa: BLE001
            pass
    return ""


def github_repo(cfg: Config) -> tuple[str, str]:
    """Owner and repo name.

    Order: .env, the Actions environment, then the git remote. The remote
    means a fresh clone works with no extra configuration.
    """
    owner = cfg.env_get("GITHUB_OWNER") or os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    repo = (
        cfg.env_get("GITHUB_REPO")
        or os.environ.get("GITHUB_REPOSITORY", "")
        or cfg.get("media_hosting", "repo", default="")
    )
    if owner and repo:
        return owner, repo

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(cfg.root),
            capture_output=True,
            text=True,
            timeout=20,
        )
        url = result.stdout.strip()
    except Exception:  # noqa: BLE001
        url = ""

    if url:
        cleaned = url.removesuffix(".git").rstrip("/")
        parts = cleaned.replace(":", "/").split("/")
        if len(parts) >= 2:
            owner = owner or parts[-2]
            repo = repo or parts[-1]
    return owner, repo


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _release_id(owner: str, repo: str, tag: str, token: str) -> int | None:
    response = requests.get(f"{API}/repos/{owner}/{repo}/releases/tags/{tag}", headers=_headers(token), timeout=60)
    if response.status_code == 200:
        return int(response.json()["id"])
    if response.status_code not in (404,):
        raise RuntimeError(f"GitHub release lookup failed {response.status_code}: {response.text[:300]}")
    create = requests.post(
        f"{API}/repos/{owner}/{repo}/releases",
        headers=_headers(token),
        json={
            "tag_name": tag,
            "name": tag,
            "body": "Automated media assets for video auto publishing",
            "draft": False,
            "prerelease": False,
        },
        timeout=60,
    )
    if create.status_code not in (200, 201):
        raise RuntimeError(f"GitHub release create failed {create.status_code}: {create.text[:300]}")
    return int(create.json()["id"])


def upload_to_github_release(cfg: Config, file: Path, tag: str, replace: bool = False) -> str:
    token = github_token(cfg)
    owner, repo = github_repo(cfg)
    if not (token and owner and repo):
        raise RuntimeError(
            "GitHub access is not configured. Either set GITHUB_TOKEN, GITHUB_OWNER and GITHUB_REPO "
            "in .env, or sign in with the GitHub CLI (gh auth login)."
        )

    release_id = _release_id(owner, repo, tag, token)
    if release_id is None:
        raise RuntimeError("Could not create or find the GitHub release")

    name = file.name
    existing = requests.get(
        f"{API}/repos/{owner}/{repo}/releases/{release_id}/assets",
        headers=_headers(token),
        timeout=60,
    )
    if existing.status_code == 200:
        for asset in existing.json():
            if asset.get("name") != name:
                continue
            same_size = asset.get("size") == file.stat().st_size
            if same_size or not replace:
                return asset["browser_download_url"]
            # the file changed, drop the old asset and upload the new one
            requests.delete(
                f"{API}/repos/{owner}/{repo}/releases/assets/{asset['id']}",
                headers=_headers(token),
                timeout=60,
            )

    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with file.open("rb") as handle:
        response = requests.post(
            f"{UPLOAD}/repos/{owner}/{repo}/releases/{release_id}/assets?name={name}",
            headers={
                **_headers(token),
                "Content-Type": content_type,
            },
            data=handle,
            timeout=1800,
        )
    if response.status_code not in (200, 201):
        # race or pagination gap: asset already exists, fetch its URL
        if response.status_code == 422 and "already_exists" in response.text:
            retry = requests.get(
                f"{API}/repos/{owner}/{repo}/releases/{release_id}/assets?per_page=100",
                headers=_headers(token),
                timeout=60,
            )
            if retry.status_code == 200:
                for asset in retry.json():
                    if asset.get("name") == name:
                        return asset["browser_download_url"]
        raise RuntimeError(f"GitHub asset upload failed {response.status_code}: {response.text[:300]}")
    return response.json()["browser_download_url"]


def raw_github_url(cfg: Config, file: Path) -> str:
    owner = cfg.env_get("GITHUB_OWNER")
    repo = cfg.env_get("GITHUB_REPO") or cfg.get("media_hosting", "repo", default="")
    branch = cfg.env_get("GITHUB_BRANCH") or "main"
    relative = file.relative_to(cfg.root).as_posix()
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{relative}"


def external_url(cfg: Config, file: Path) -> str:
    base = cfg.get("media_hosting", "external_base_url", default="") or cfg.env_get("MEDIA_BASE_URL")
    if not base:
        raise RuntimeError("No external_base_url configured")
    return f"{base.rstrip('/')}/{file.name}"


def public_url(cfg: Config, file: Path, tag: str) -> str:
    method = cfg.get("media_hosting", "method", default="github_release")
    if method == "github_release":
        return upload_to_github_release(cfg, file, tag)
    if method == "raw_github":
        return raw_github_url(cfg, file)
    if method == "external":
        return external_url(cfg, file)
    raise RuntimeError(f"Unknown media_hosting method: {method}")
