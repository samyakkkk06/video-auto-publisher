"""Convert source clips into platform ready vertical video with ffmpeg."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import Config

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def ffmpeg_available() -> bool:
    try:
        subprocess.run(
            [FFMPEG, "-version"],
            check=True,
            capture_output=True,
            timeout=20,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def build_filter(cfg: Config) -> str:
    width = int(cfg.get("media", "width", default=1080))
    height = int(cfg.get("media", "height", default=1920))
    fps = int(cfg.get("media", "fps", default=30))
    fit = str(cfg.get("media", "fit", default="crop"))
    if fit == "pad":
        # keeps the whole frame, letterboxed
        video_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
    else:
        # fills the frame and crops the overflow (best for Reels)
        video_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
        )
    return f"{video_filter},setsar=1,fps={fps},unsharp=5:5:0.7:5:5:0.0"


def prepare_video(cfg: Config, source: Path, force: bool = False) -> Path:
    """Return a 9:16 h264/aac version of the clip, ready for Reels and Shorts."""
    target = cfg.media_dir / f"{source.stem}_reel.mp4"
    if target.exists() and not force:
        return target

    if not ffmpeg_available():
        shutil.copy2(source, target)
        return target

    crf = str(cfg.get("media", "video_crf", default=23))
    preset = str(cfg.get("media", "preset", default="medium"))
    audio_bitrate = str(cfg.get("media", "audio_bitrate", default="128k"))

    command = [
        FFMPEG,
        "-y",
        "-i",
        str(source),
        "-vf",
        build_filter(cfg),
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        crf,
        "-profile:v",
        "high",
        "-level",
        "4.0",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-ar",
        "44100",
        "-movflags",
        "+faststart",
        str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0 or not target.exists():
        raise RuntimeError(f"ffmpeg failed for {source.name}: {result.stderr[-600:]}")
    return target


def make_thumbnail(video: Path, output: Path, at_seconds: float = 1.0) -> Path | None:
    """Grab a still frame for the YouTube thumbnail. Optional, failures ignored."""
    if not ffmpeg_available():
        return None
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        FFMPEG,
        "-y",
        "-ss",
        str(at_seconds),
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(output),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and output.exists():
            return output
    except Exception:  # noqa: BLE001
        pass
    return None
