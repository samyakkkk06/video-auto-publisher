"""Small helpers: state tracking, logging, notifications."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from .config import Config


def load_state(cfg: Config) -> dict[str, Any]:
    if cfg.state_file.exists():
        try:
            # utf-8-sig strips a BOM if a Windows tool added one
            return json.loads(cfg.state_file.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
    return {}


def save_state(cfg: Config, state: dict[str, Any]) -> None:
    cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def log(cfg: Config, message: str, level: str = "INFO") -> None:
    stamp = datetime.now().isoformat(timespec="seconds")
    line = f"{stamp} [{level}] {message}"
    print(line, flush=True)
    try:
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        log_file = cfg.log_dir / f"{datetime.now().date()}.log"
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def notify(cfg: Config, message: str) -> None:
    """Optional Telegram alert. Silent failure, never blocks publishing."""
    token = cfg.env_get("TELEGRAM_BOT_TOKEN")
    chat_id = cfg.env_get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=20,
        )
    except Exception:  # noqa: BLE001
        pass


def commit_state(cfg: Config, message: str = "chore: update publishing state") -> None:
    """Persist state back to the git repo (used by the cloud workflow)."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    import subprocess

    try:
        subprocess.run(["git", "config", "user.name", "VideoAutoPublisher"], check=False, capture_output=True)
        subprocess.run(["git", "config", "user.email", "autopublish@users.noreply.github.com"], check=False, capture_output=True)
        subprocess.run(["git", "add", str(cfg.state_file)], check=True, capture_output=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if diff.returncode != 0:
            subprocess.run(["git", "commit", "-m", message], check=True, capture_output=True)
            subprocess.run(
                ["git", "push", "origin", "HEAD"],
                check=True,
                capture_output=True,
                env={**os.environ, "GIT_ASKPASS": "echo"},
            )
            log(cfg, "State committed and pushed to repository")
    except Exception as exc:  # noqa: BLE001
        log(cfg, f"Could not commit state: {exc}", "WARN")
