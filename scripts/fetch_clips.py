"""Download the clips listed in clips_manifest.json from the GitHub Release.

The cloud runner calls this before every publish run so the clips are present
without ever putting video files into git history.

Run:  python -m scripts.fetch_clips
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from .config import Config, load_config
from .state import log

MANIFEST_NAME = "clips_manifest.json"
CHUNK = 1024 * 1024


def manifest_path(cfg: Config) -> Path:
    return cfg.root / MANIFEST_NAME


def load_manifest(cfg: Config) -> dict:
    path = manifest_path(cfg)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _download_one(item: dict, target_dir: Path, force: bool) -> tuple[str, str]:
    name = item.get("name") or item.get("asset")
    url = item.get("url")
    size = int(item.get("size") or 0)
    if not name or not url:
        return "skip", "manifest entry missing name or url"

    target = target_dir / name
    if target.exists() and not force and (size == 0 or target.stat().st_size == size):
        return "cached", name

    target_dir.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".part")
    try:
        with requests.get(url, stream=True, timeout=180) as response:
            if response.status_code >= 400:
                return "error", f"{name}: HTTP {response.status_code}"
            with temp.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=CHUNK):
                    if chunk:
                        handle.write(chunk)
        if size and temp.stat().st_size != size:
            temp.unlink(missing_ok=True)
            return "error", f"{name}: size mismatch (expected {size}, got {temp.stat().st_size})"
        temp.replace(target)
        return "ok", name
    except Exception as exc:  # noqa: BLE001
        temp.unlink(missing_ok=True)
        return "error", f"{name}: {exc}"


def fetch(cfg: Config, force: bool = False) -> int:
    manifest = load_manifest(cfg)
    clips = manifest.get("clips") or []
    if not clips:
        print("No clips_manifest.json found or it lists no clips.")
        return 0

    print(f"Fetching {len(clips)} clips from the GitHub Release...\n")
    failed = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda item: _download_one(item, cfg.clips_dir, force), clips)
        )

    for status, message in results:
        if status == "error":
            failed += 1
            print(f"  FAILED  {message}")
        elif status == "cached":
            print(f"  cached  {message}")
        else:
            print(f"  ok      {message}")

    print(f"\n{len(clips) - failed}/{len(clips)} clips ready in {cfg.clips_dir}")
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Download clips from the GitHub Release")
    parser.add_argument("--force", action="store_true", help="re-download even if the file exists")
    args = parser.parse_args()

    cfg = load_config()
    failed = fetch(cfg, force=args.force)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
