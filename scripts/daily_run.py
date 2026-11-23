"""Main orchestrator.

One run does everything:
  1. build today's queue (5 clips + matching descriptions)
  2. generate captions / descriptions / tags with AI
  3. render vertical video for Reels and Shorts
  4. upload to YouTube with a scheduled publish time
  5. publish to Instagram when its slot time arrives

Safe to run as often as you like, it never double posts.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import content_generator as cg
from . import media_hosting
from . import media_prep
from .ai_client import provider_status
from .config import Config, load_config
from .state import commit_state, load_state, log, notify, save_state

try:
    from . import publisher_instagram as ig
    from . import publisher_youtube as yt
except Exception:  # noqa: BLE001
    ig = None  # type: ignore
    yt = None  # type: ignore

# Windows consoles default to cp1252, which cannot print emoji or accents
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# --------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------
def now_in(cfg: Config) -> datetime:
    return datetime.now(ZoneInfo(cfg.timezone_name))


def slot_datetimes(cfg: Config, day: datetime) -> list[tuple[str, datetime]]:
    tz = ZoneInfo(cfg.timezone_name)
    result = []
    for time_str in cfg.schedule_times:
        hour, minute = (int(part) for part in time_str.split(":"))
        stamp = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        result.append((time_str, stamp.astimezone(tz)))
    return sorted(result, key=lambda item: item[1])


# --------------------------------------------------------------------------
# queue building
# --------------------------------------------------------------------------
def build_queue(cfg: Config, state: dict, day: datetime) -> dict:
    """Create or repair today's queue of clips."""
    day_key = day.strftime("%Y-%m-%d")
    days = state.setdefault("days", {})
    entry = days.setdefault(day_key, {"slots": {}})

    all_clips = cg.load_clips(cfg)
    if not all_clips:
        # the clips live in a GitHub Release, pull them down
        try:
            from . import fetch_clips

            if fetch_clips.fetch(cfg) == 0:
                all_clips = cg.load_clips(cfg)
        except Exception as exc:  # noqa: BLE001
            log(cfg, f"Could not fetch clips from the release: {exc}", "WARN")

    if not all_clips:
        log(cfg, "No clips available. Nothing to publish.", "WARN")
        return entry

    blocks = cg.load_description_blocks(cfg)
    used = set(state.get("used_clips", []))

    count = min(cfg.posts_per_day, len(cfg.schedule_times), len(all_clips))
    slots = cfg.schedule_times[:count]

    # prefer clips never used before, then least recently used
    ordered = sorted(
        all_clips,
        key=lambda p: (p.name in used, list(all_clips).index(p)),
    )
    selected = ordered[:count]

    for index, time_str in enumerate(slots):
        clip = selected[index]
        slot = entry["slots"].setdefault(time_str, {})
        slot["clip"] = clip.name
        clip_index = all_clips.index(clip)
        if blocks:
            description = blocks[clip_index] if clip_index < len(blocks) else blocks[clip_index % len(blocks)]
        else:
            description = {
                "title": "",
                "text": f"Short form video clip named {clip.stem.replace('_', ' ')}.",
                "filename": "",
            }
        slot["description"] = description

    entry["queue_size"] = count
    state.setdefault("used_clips", [])
    for clip in selected:
        if clip.name not in state["used_clips"]:
            state["used_clips"].append(clip.name)
    return entry


def clip_by_name(cfg: Config, name: str) -> Path | None:
    for clip in cg.load_clips(cfg):
        if clip.name == name:
            return clip
    return None


# --------------------------------------------------------------------------
# content + media
# --------------------------------------------------------------------------
def ensure_content(cfg: Config, slot: dict, force: bool = False) -> dict:
    clip = clip_by_name(cfg, slot["clip"])
    if clip is None:
        raise RuntimeError(f"Clip missing from disk: {slot['clip']}")
    description = slot.get("description") or {"title": "", "text": "", "filename": ""}
    if isinstance(description, str):
        description = {"title": "", "text": description, "filename": ""}
    content = cg.generate_for_clip(cfg, clip, description, force=force)
    slot["content"] = content
    return content


def ensure_media(cfg: Config, slot: dict) -> Path:
    clip = clip_by_name(cfg, slot["clip"])
    if clip is None:
        raise RuntimeError(f"Clip missing from disk: {slot['clip']}")
    prepared = media_prep.prepare_video(cfg, clip)
    slot["media_path"] = str(prepared.relative_to(cfg.root))
    return prepared


def ensure_public_url(cfg: Config, slot: dict, tag: str) -> str:
    prepared = Path(cfg.root / slot["media_path"])
    url = media_hosting.public_url(cfg, prepared, tag)
    slot["public_url"] = url
    return url


# --------------------------------------------------------------------------
# publishing
# --------------------------------------------------------------------------
def publish_youtube(cfg: Config, slot: dict, publish_at: datetime, force: bool = False) -> None:
    if not cfg.get("platforms", "youtube", "enabled", default=True):
        return
    existing = slot.get("youtube")
    if existing and existing.get("video_id") and not force:
        return
    if yt is None:
        log(cfg, "YouTube module unavailable (dependencies missing)", "WARN")
        return

    content = slot["content"]
    video_path = Path(cfg.root / slot["media_path"])
    title = content["youtube"]["title"]
    description = content["youtube"]["description"]
    tags = content["youtube"]["tags"]

    thumbnail = media_prep.make_thumbnail(
        video_path, cfg.media_dir / f"{video_path.stem}.jpg", at_seconds=1.0
    )

    result = yt.upload_video(
        cfg,
        video_path=video_path,
        title=title,
        description=description,
        tags=tags,
        publish_at=publish_at if publish_at > now_in(cfg) else None,
        privacy=cfg.get("platforms", "youtube", "privacy_status", default="public"),
        thumbnail=thumbnail,
    )
    slot["youtube"] = {
        **result,
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
    }
    log(cfg, f"YouTube upload OK: {title} -> {result.get('url')}")


def publish_instagram(cfg: Config, slot: dict, force: bool = False) -> None:
    if not cfg.get("platforms", "instagram", "enabled", default=True):
        return
    existing = slot.get("instagram")
    if existing and existing.get("media_id") and not force:
        return
    if ig is None:
        log(cfg, "Instagram module unavailable (dependencies missing)", "WARN")
        return
    if not slot.get("public_url"):
        raise RuntimeError("No public media URL available for Instagram")

    content = slot["content"]
    result = ig.publish_reel(
        cfg,
        video_path=Path(cfg.root / slot["media_path"]),
        video_url=slot["public_url"],
        caption=content["instagram"]["caption"],
    )
    slot["instagram"] = {
        **result,
        "published_at": datetime.now().isoformat(timespec="seconds"),
    }
    log(cfg, f"Instagram publish OK: {result.get('permalink') or result.get('media_id')}")


# --------------------------------------------------------------------------
# main flow
# --------------------------------------------------------------------------
def run(cfg: Config, dry_run: bool = False, force: bool = False, only: str = "all") -> None:
    state = load_state(cfg)
    now = now_in(cfg)
    day_key = now.strftime("%Y-%m-%d")
    log(cfg, f"Run started at {now.isoformat(timespec='seconds')} ({cfg.timezone_name})")
    log(cfg, f"AI providers available: {', '.join(provider_status())}")

    entry = build_queue(cfg, state, now)
    slots_map = entry.get("slots", {})
    tag = f"{cfg.get('media_hosting', 'release_tag_prefix', default='media')}-{day_key}"

    # persist the queue immediately so a crash never loses the plan
    save_state(cfg, state)

    for time_str, slot_time in slot_datetimes(cfg, now):
        slot = slots_map.get(time_str)
        if not slot:
            continue

        # 1. content
        try:
            ensure_content(cfg, slot, force=force)
            save_state(cfg, state)
        except Exception as exc:  # noqa: BLE001
            log(cfg, f"Content generation failed for {time_str}: {exc}", "ERROR")
            continue

        # 2. media
        try:
            ensure_media(cfg, slot)
            save_state(cfg, state)
        except Exception as exc:  # noqa: BLE001
            log(cfg, f"Media prep failed for {time_str}: {exc}", "ERROR")
            continue

        is_due = now >= slot_time
        window = timedelta(minutes=cfg.publish_window_minutes)
        is_past_due = now <= slot_time + window
        due_now = is_due and is_past_due

        # 3. YouTube: schedule ahead, publish immediately if the slot passed
        try:
            if not dry_run:
                publish_youtube(cfg, slot, slot_time, force=force)
            else:
                slot.setdefault("youtube", {})["planned"] = slot_time.isoformat()
            save_state(cfg, state)
        except Exception as exc:  # noqa: BLE001
            log(cfg, f"YouTube failed for {time_str}: {exc}", "ERROR")

        # 4. Instagram: only when the slot time arrives
        if only in ("all", "instagram") and due_now:
            try:
                if not dry_run:
                    ensure_public_url(cfg, slot, tag)
                    publish_instagram(cfg, slot, force=force)
                else:
                    slot.setdefault("instagram", {})["planned"] = slot_time.isoformat()
                save_state(cfg, state)
            except Exception as exc:  # noqa: BLE001
                log(cfg, f"Instagram failed for {time_str}: {exc}", "ERROR")

    save_state(cfg, state)
    if not dry_run:
        commit_state(cfg, f"chore: publishing state {day_key}")
        summary = [
            f"{time_str} | {slot.get('clip')} | YT:{'ok' if slot.get('youtube', {}).get('video_id') else '-'} "
            f"| IG:{'ok' if slot.get('instagram', {}).get('media_id') else '-'}"
            for time_str, slot in sorted(slots_map.items())
        ]
        notify(cfg, f"Publishing run done for {day_key}\n" + "\n".join(summary))
    log(cfg, "Run finished")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def verify(cfg: Config) -> None:
    """Check every credential and connection without publishing anything."""
    print("\n=== SYSTEM CHECK ===\n")

    from .ai_client import provider_status

    providers = provider_status()
    print(f"AI providers       : {', '.join(providers)}")
    if providers == ["local-template"]:
        print("  ! No AI key found. Captions will use the offline template writer.")
        print("    Add GEMINI_API_KEY or GROQ_API_KEY to .env for real AI writing.")

    clips = cg.load_clips(cfg)
    blocks = cg.load_description_blocks(cfg)
    print(f"Clips found        : {len(clips)}")
    print(f"Description blocks : {len(blocks)}")
    if not clips:
        print("  ! Put your 30-45 second clips in the clips/ folder.")
    if not blocks:
        print("  ! Put your Word file in the descriptions/ folder.")

    if yt is not None:
        try:
            items = yt.list_uploads(cfg, max_results=3)
            print(f"YouTube API        : OK (channel reachable, {len(items)} recent uploads)")
        except Exception as exc:  # noqa: BLE001
            print(f"YouTube API        : NOT READY -> {exc}")
    else:
        print("YouTube API        : python dependencies not installed (run setup.ps1)")

    if ig is not None:
        try:
            info = ig.account_info(cfg)
            print(f"Instagram API      : OK (@{info.get('username')}, id {info.get('id')})")
        except Exception as exc:  # noqa: BLE001
            print(f"Instagram API      : NOT READY -> {exc}")
    else:
        print("Instagram API      : python dependencies not installed (run setup.ps1)")

    method = cfg.get("media_hosting", "method", default="github_release")
    owner = cfg.env_get("GITHUB_OWNER")
    repo = cfg.env_get("GITHUB_REPO") or cfg.get("media_hosting", "repo", default="")
    print(f"Media hosting      : {method} (repo {owner}/{repo or 'not set'})")

    print("\n=== SCHEDULE ===\n")
    now = now_in(cfg)
    for time_str, slot_time in slot_datetimes(cfg, now):
        marker = "due" if now >= slot_time else "later"
        print(f"  {time_str} {cfg.timezone_name}  ({marker})")
    print()


def test_ai(cfg: Config) -> None:
    """Generate one sample caption to prove the AI key works."""
    from .ai_client import generate_json
    from .content_generator import SYSTEM_PROMPT, build_user_prompt, normalise

    sample = {
        "title": "sample title",
        "text": (
            "The camera pans slowly across a quiet beach at sunrise. Small waves roll in and "
            "catch the light. A kite surfer in the distance catches a wave and glides across "
            "the frame."
        ),
        "filename": "sample_clip.mp4",
    }
    print("\nAI providers available:", ", ".join(provider_status()))
    print("\nGenerating a sample caption...\n")
    try:
        raw = generate_json(SYSTEM_PROMPT, build_user_prompt(sample, cfg, "sample_clip.mp4"))
        content = normalise(raw, cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"AI call failed: {exc}")
        print("\nCheck the key in .env, then run this again.")
        return

    print("INSTAGRAM CAPTION")
    print("-" * 60)
    print(content["instagram"]["caption"])
    print()
    print("YOUTUBE TITLE")
    print("-" * 60)
    print(content["youtube"]["title"])
    print()
    print("YOUTUBE DESCRIPTION")
    print("-" * 60)
    print(content["youtube"]["description"])
    print()
    print("TAGS")
    print("-" * 60)
    print(", ".join(content["youtube"]["tags"]))
    print("\nAI IS WORKING\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Automated video publishing")
    parser.add_argument("--dry-run", action="store_true", help="plan only, no API calls")
    parser.add_argument("--force", action="store_true", help="ignore caches, republish")
    parser.add_argument("--only", choices=["all", "instagram", "youtube"], default="all")
    parser.add_argument("--verify", action="store_true", help="check credentials and setup")
    parser.add_argument("--test-ai", action="store_true", help="generate a sample caption to test the AI key")
    parser.add_argument("--plan", action="store_true", help="print today's plan and exit")
    args = parser.parse_args()

    cfg = load_config()

    if args.verify:
        verify(cfg)
        return 0

    if args.test_ai:
        test_ai(cfg)
        return 0

    if args.plan:
        state = load_state(cfg)
        now = now_in(cfg)
        entry = build_queue(cfg, state, now)
        print(json.dumps(entry.get("slots", {}), indent=2, ensure_ascii=False))
        return 0

    run(cfg, dry_run=args.dry_run, force=args.force, only=args.only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
