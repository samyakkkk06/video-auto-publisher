"""Push your clips to a GitHub Release and write the small manifest that git tracks.

Why this exists: video files in git history bloat the repository forever.
A GitHub Release stores the same files, free, without touching git history.
The cloud runner downloads them from the release before every publish run.

Run:  python -m scripts.upload_clips
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import media_hosting
from .config import Config, load_config
from .content_generator import VIDEO_EXTENSIONS, _natural_key, load_clips
from .state import log

MANIFEST_NAME = "clips_manifest.json"
RELEASE_TAG = "clips"


def manifest_path(cfg: Config) -> Path:
    return cfg.root / MANIFEST_NAME


def load_manifest(cfg: Config) -> dict:
    path = manifest_path(cfg)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_manifest(cfg: Config, manifest: dict) -> None:
    manifest_path(cfg).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def upload(cfg: Config, replace: bool = True, tag: str = RELEASE_TAG) -> dict:
    clips = load_clips(cfg)
    if not clips:
        print("No clips found in clips/. Nothing to upload.")
        return load_manifest(cfg)

    manifest = load_manifest(cfg)
    manifest.setdefault("release_tag", tag)
    manifest.setdefault("clips", [])

    known = {item["name"]: item for item in manifest["clips"]}
    results = []

    for clip in clips:
        size = clip.stat().st_size
        existing = known.get(clip.name)
        if existing and existing.get("size") == size and existing.get("url"):
            print(f"  unchanged  {clip.name}")
            results.append(existing)
            continue

        print(f"  uploading  {clip.name} ({size / 1_000_000:.1f} MB) ...")
        url = media_hosting.upload_to_github_release(cfg, clip, tag, replace=replace)
        item = {
            "name": clip.name,
            "asset": clip.name,
            "url": url,
            "size": size,
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        }
        results.append(item)
        print(f"    -> {url}")

    manifest["clips"] = results
    manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
    manifest["clip_count"] = len(results)
    manifest["total_bytes"] = sum(item.get("size", 0) for item in results)
    save_manifest(cfg, manifest)
    return manifest


def push_manifest(cfg: Config) -> None:
    try:
        subprocess.run(["git", "add", MANIFEST_NAME], check=True, capture_output=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if diff.returncode != 0:
            subprocess.run(["git", "commit", "-m", f"chore: clip manifest ({len(load_manifest(cfg).get('clips', []))} clips)"], check=True, capture_output=True)
            subprocess.run(["git", "push"], check=True, capture_output=True)
            print("Manifest committed and pushed.")
        else:
            print("Manifest already up to date.")
    except subprocess.CalledProcessError as exc:
        print(f"Could not push the manifest: {exc.stderr.decode(errors='ignore')[:300]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload clips to a GitHub Release")
    parser.add_argument("--no-push", action="store_true", help="do not commit and push the manifest")
    parser.add_argument("--tag", default=RELEASE_TAG, help="release tag to use")
    args = parser.parse_args()

    cfg = load_config()
    print("\nUploading clips to GitHub Release...\n")
    manifest = upload(cfg, tag=args.tag)
    print(f"\nManifest written: {MANIFEST_NAME} ({manifest.get('clip_count', 0)} clips)")
    if not args.no_push:
        push_manifest(cfg)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
