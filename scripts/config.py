"""Configuration + environment loading."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv() -> dict[str, str]:
    """Minimal .env loader (no dependency needed)."""
    env: dict[str, str] = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            env[key] = value
            os.environ.setdefault(key, value)
    return env


@dataclass
class Config:
    data: dict[str, Any]
    env: dict[str, str] = field(default_factory=dict)

    # ---------- access helpers ----------
    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def env_get(self, key: str, default: str = "") -> str:
        return (self.env.get(key) or os.environ.get(key) or default).strip()

    # ---------- paths ----------
    @property
    def root(self) -> Path:
        return ROOT

    @property
    def clips_dir(self) -> Path:
        return ROOT / self.get("clips_dir", default="clips")

    @property
    def descriptions_dir(self) -> Path:
        return ROOT / self.get("descriptions_dir", default="descriptions")

    @property
    def output_dir(self) -> Path:
        return ROOT / self.get("output_dir", default="output")

    @property
    def metadata_dir(self) -> Path:
        return self.output_dir / "metadata"

    @property
    def media_dir(self) -> Path:
        return self.output_dir / "media"

    @property
    def state_file(self) -> Path:
        return ROOT / self.get("state_file", default="state/published.json")

    @property
    def log_dir(self) -> Path:
        return self.output_dir / "logs"

    # ---------- schedule ----------
    @property
    def timezone_name(self) -> str:
        return self.get("timezone", default="America/New_York")

    @property
    def schedule_times(self) -> list[str]:
        raw = self.get("schedule_times", default=["07:00", "11:00", "16:00", "20:00", "00:00"])
        cleaned = []
        for item in raw:
            value = str(item).strip()
            parts = value.split(":")
            if len(parts) == 2:
                cleaned.append(f"{int(parts[0]):02d}:{int(parts[1]):02d}")
        return sorted(set(cleaned))

    @property
    def posts_per_day(self) -> int:
        return int(self.get("posts_per_day", default=len(self.schedule_times)))

    @property
    def publish_window_minutes(self) -> int:
        return int(self.get("publish_window_minutes", default=30))

    def ensure_dirs(self) -> None:
        for path in (
            self.clips_dir,
            self.descriptions_dir,
            self.output_dir,
            self.metadata_dir,
            self.media_dir,
            self.log_dir,
            self.state_file.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    env = load_dotenv()
    config_path = ROOT / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"config.json missing at {config_path}. Copy config.example.json -> config.json"
        )
    data = json.loads(config_path.read_text(encoding="utf-8"))
    cfg = Config(data=data, env=env)
    cfg.ensure_dirs()
    return cfg
