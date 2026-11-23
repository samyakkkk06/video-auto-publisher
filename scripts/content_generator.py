"""Read clip descriptions (Word / text) and generate platform content with AI."""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .ai_client import AIError, generate_json
from .config import Config

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".mpg", ".mpeg"}
DESCRIPTION_EXTENSIONS = {".docx", ".doc", ".html", ".htm", ".txt", ".md", ".rtf", ".json"}

SYSTEM_PROMPT = """You are a senior social media strategist who writes for short form video.
You write like a real person posting from their phone, never like a brand press release.

Hard rules:
- Never use em dashes, never use the words "delve", "leverage", "game changer", "unlock", "elevate", "revolutionary", "seamless", "cutting edge", "tapestry".
- No corporate jargon, no fake urgency, no "smash that like button".
- Keep sentences short. Sound human. Use at most 2 emoji in an Instagram caption.
- Never invent facts, names, numbers or results that are not in the source description.
- Return valid JSON only, no markdown fences, no commentary."""


# --------------------------------------------------------------------------
# description loading
# --------------------------------------------------------------------------
def _docx_blocks(path: Path) -> list[dict]:
    try:
        import docx  # type: ignore
    except ImportError:
        return []
    document = docx.Document(str(path))
    blocks: list[dict] = []
    current: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            if current:
                blocks.append({"title": "", "text": "\n".join(current), "filename": ""})
                current = []
            continue
        current.append(text)
    if current:
        blocks.append({"title": "", "text": "\n".join(current), "filename": ""})
    if not blocks:
        for table in document.tables:
            rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
            if rows:
                blocks.append({"title": "", "text": "\n".join(rows), "filename": ""})
    return blocks


def _strip_html(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def _html_blocks(path: Path) -> list[dict]:
    """Google Docs, Word and Chrome all export .doc as HTML. Parse the structure."""
    raw = path.read_text(encoding="utf-8-sig", errors="ignore")
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    raw = re.sub(r"<hr\b[^>]*>", "\n@@BLOCK@@\n", raw, flags=re.I)

    blocks: list[dict] = []
    for chunk in raw.split("@@BLOCK@@"):
        parts = re.findall(
            r"<(p|h[1-6])\b([^>]*)>(.*?)</\1>", chunk, flags=re.S | re.I
        )
        heading = ""
        lines: list[str] = []
        filename = ""
        title = ""

        for tag, attrs, inner in parts:
            tag = tag.lower()
            css_class = ""
            class_match = re.search(r"class\s*=\s*[\"']([^\"']*)[\"']", attrs, flags=re.I)
            if class_match:
                css_class = class_match.group(1).lower()

            # a bold label such as <b>Title</b><br> tells us what the text is
            label = ""
            body_html = inner
            label_match = re.match(
                r"^\s*<(?:b|strong)\b[^>]*>(.*?)</(?:b|strong)>\s*(?:<br\s*/?>\s*)?",
                inner,
                flags=re.S | re.I,
            )
            if label_match:
                label = _strip_html(label_match.group(1)).lower().rstrip(":")
                body_html = inner[label_match.end():]

            text = _strip_html(body_html)
            if not text:
                continue

            if tag == "h1":
                continue
            if tag == "h2":
                heading = text
                continue
            if "f" in css_class.split() and re.search(r"\.(mp4|mov|m4v|webm)$", text, flags=re.I):
                filename = text
                continue
            if "m" in css_class.split():
                continue
            if label == "title":
                title = text
                lines.append(text)
                continue
            if label == "description":
                lines.append(text)
                continue
            lines.append(text)

        body = "\n".join(lines).strip()
        if not body:
            continue
        blocks.append({"title": title or heading, "text": body, "filename": filename})
    return blocks


def _rtf_blocks(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    raw = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), raw)
    raw = re.sub(r"\\par[d]?\b", "\n", raw)
    raw = re.sub(r"\\[a-zA-Z]+-?\d*\s?", " ", raw)
    raw = raw.replace("{", "").replace("}", "")
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", raw) if chunk.strip()]
    return [{"title": "", "text": chunk, "filename": ""} for chunk in chunks]


def _txt_blocks(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig", errors="ignore")
    chunks = re.split(r"\n\s*\n", text)
    return [
        {"title": "", "text": chunk.strip(), "filename": ""}
        for chunk in chunks
        if chunk.strip()
    ]


def _is_placeholder(path: Path) -> bool:
    name = path.name.lower()
    return name.startswith("_") or name.startswith("readme") or name.startswith("put_") or name.startswith("place")


def load_description_blocks(cfg: Config) -> list[dict]:
    """Return description blocks as dicts with title, text and filename."""
    blocks: list[dict] = []
    files = sorted(
        p
        for p in cfg.descriptions_dir.iterdir()
        if p.suffix.lower() in DESCRIPTION_EXTENSIONS and not _is_placeholder(p)
    )
    for file in files:
        suffix = file.suffix.lower()
        try:
            head = file.read_bytes()[:400]
        except OSError:
            head = b""

        head = head.lstrip(b"\xef\xbb\xbf \t\r\n")

        if suffix == ".docx":
            file_blocks = _docx_blocks(file)
        elif suffix == ".json":
            file_blocks = []
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data = list(data.values())
                for item in data:
                    if isinstance(item, dict):
                        file_blocks.append(
                            {
                                "title": str(item.get("title", "")),
                                "text": str(item.get("description") or item.get("text") or ""),
                                "filename": str(item.get("filename", "")),
                            }
                        )
                    elif str(item).strip():
                        file_blocks.append({"title": "", "text": str(item).strip(), "filename": ""})
            except json.JSONDecodeError:
                pass
        elif head[:200].lower().startswith((b"<!doctype", b"<html", b"<head", b"<body")) or suffix in (".html", ".htm"):
            file_blocks = _html_blocks(file)
        elif head.startswith(b"{\\rtf"):
            file_blocks = _rtf_blocks(file)
        else:
            file_blocks = _txt_blocks(file)

        blocks.extend(block for block in file_blocks if block.get("text"))
    return blocks


def _natural_key(path: Path):
    """Sort so 'clip 2' comes before 'clip 10'."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    ]


def load_clips(cfg: Config) -> list[Path]:
    return sorted(
        (
            p
            for p in cfg.clips_dir.iterdir()
            if p.suffix.lower() in VIDEO_EXTENSIONS and not _is_placeholder(p)
        ),
        key=_natural_key,
    )


# --------------------------------------------------------------------------
# prompt
# --------------------------------------------------------------------------
def build_user_prompt(description: str, cfg: Config, clip_name: str) -> str:
    content_cfg = cfg.get("content", default={}) or {}
    audience = content_cfg.get("audience", "global Tier 1 audience")
    voice = content_cfg.get("brand_voice", "conversational")
    language = content_cfg.get("language", "English")
    niche = content_cfg.get("niche", "")
    extra = content_cfg.get("extra_instructions", "")
    hashtag_count = int(cfg.get("platforms", "instagram", "hashtag_count", default=10))
    caption_max = int(cfg.get("platforms", "instagram", "caption_max_chars", default=2200))

    source_title = ""
    source_text = description
    if isinstance(description, dict):
        source_title = str(description.get("title") or "")
        source_text = str(description.get("text") or "")

    title_context = (
        f'\nORIGINAL TITLE FROM THE CREATOR (use it as context, you may improve on it):\n"{source_title}"\n'
        if source_title
        else ""
    )

    return f"""Write the publishing copy for one short video clip (30 to 45 seconds).

CLIP FILE: {clip_name}
{title_context}
SOURCE DESCRIPTION (this is the only truth you have about the video):
\"\"\"
{source_text}
\"\"\"

AUDIENCE: {audience}
LANGUAGE: {language}
BRAND VOICE: {voice}
NICHE: {niche or "not specified"}
EXTRA INSTRUCTIONS: {extra or "none"}

Return this exact JSON structure:

{{
  "instagram": {{
    "caption": "First line is a strong hook under 8 words. Then 2 to 5 short lines describing what happens in the clip. Then one short call to action. No hashtag inside the caption body.",
    "hashtags": ["exactly {hashtag_count} hashtags without the # symbol. Mix 3 broad, 4 mid size, {max(1, hashtag_count - 7)} niche specific. All lowercase friendly. No banned or spam tags."]
  }},
  "youtube": {{
    "title": "one title, under 95 characters, curiosity driven, no clickbait lie, no emoji, no ALL CAPS spam",
    "title_options": ["3 alternative titles, each under 95 characters"],
    "description": "SEO description. First 2 lines must work as the visible hook and contain the main keyword. Then 3 to 6 lines of context. Then a single call to action line. Then a keywords line. Total under {caption_max} characters.",
    "tags": ["up to 15 short tags, each 2 to 4 words, no # symbol, total character count under 480"],
    "keywords": ["5 to 10 search keywords a viewer would actually type"],
    "hashtags": ["3 hashtags for the YouTube description, no # symbol"]
  }},
  "content_summary": "one sentence, what this clip actually shows",
  "topics": ["3 to 5 short topic keywords"]
}}

Rules for the caption and description:
- Describe only what the source description supports. If it is vague, stay general and do not invent specifics.
- Write like a person who actually watched the clip.
- Keep the Instagram caption under 700 characters.
"""


# --------------------------------------------------------------------------
# validation + normalisation
# --------------------------------------------------------------------------
def _clean_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip().lstrip("#")
        text = re.sub(r"\s+", " ", text)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _clean_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    text = text.replace("—", "-").replace("–", "-")
    return text[:limit]


def normalise(payload: dict[str, Any], cfg: Config) -> dict[str, Any]:
    hashtag_count = int(cfg.get("platforms", "instagram", "hashtag_count", default=10))
    caption_max = int(cfg.get("platforms", "instagram", "caption_max_chars", default=2200))

    instagram = payload.get("instagram") or {}
    youtube = payload.get("youtube") or {}

    hashtags = _clean_list(instagram.get("hashtags"), hashtag_count)
    caption = _clean_text(instagram.get("caption"), caption_max)
    if hashtags and "#" not in caption:
        caption = caption + "\n\n" + " ".join(f"#{tag}" for tag in hashtags)

    yt_hashtags = _clean_list(youtube.get("hashtags"), 5)
    yt_description = _clean_text(youtube.get("description"), 5000)
    if yt_hashtags:
        yt_description = yt_description + "\n\n" + " ".join(f"#{tag}" for tag in yt_hashtags)

    tags = _clean_list(youtube.get("tags"), 15)
    default_tags = cfg.get("platforms", "youtube", "default_tags", default=[]) or []
    for tag in default_tags:
        if tag not in tags:
            tags.append(tag)
    tags = tags[:15]

    titles = _clean_list(youtube.get("title_options"), 3)
    title = _clean_text(youtube.get("title"), 100)
    if title and title not in titles:
        titles.insert(0, title)
    titles = titles[:3]

    return {
        "instagram": {
            "caption": caption,
            "hashtags": hashtags,
        },
        "youtube": {
            "title": title or (titles[0] if titles else "New clip"),
            "title_options": titles,
            "description": yt_description,
            "tags": tags,
            "keywords": _clean_list(youtube.get("keywords"), 12),
        },
        "content_summary": _clean_text(payload.get("content_summary"), 400),
        "topics": _clean_list(payload.get("topics"), 6),
    }


def fallback_content(description: str, cfg: Config) -> dict[str, Any]:
    """Deterministic writer used when no AI provider is reachable."""
    text = " ".join(description.split())
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    hook = (sentences[0] if sentences else "A moment worth watching").rstrip(".")[:78]
    body = " ".join(sentences[1:3]) if len(sentences) > 1 else ""
    if body:
        body = body.rstrip(".") + "."
    body = body[:220]
    summary = text[:240].rstrip(".")

    hashtag_count = int(cfg.get("platforms", "instagram", "hashtag_count", default=10))
    base_tags = [
        "reels",
        "shorts",
        "viralvideo",
        "fyp",
        "trending",
        "explore",
        "reelsinstagram",
        "youtubeshorts",
        "dailyvideo",
        "watchtillend",
    ]
    stopwords = {
        "this", "that", "with", "from", "into", "while", "they", "them", "their",
        "there", "here", "what", "when", "where", "which", "about", "after",
        "before", "camera", "frame", "shot", "clip",
    }
    topics = [
        word.lower()
        for word in re.findall(r"[A-Za-z]{4,}", text)
        if word.lower() not in stopwords
    ]
    seen: list[str] = []
    for topic in topics:
        if topic not in seen:
            seen.append(topic)
    topics = seen[:6]
    hashtags = (base_tags + topics)[:hashtag_count]

    caption = hook
    if body:
        caption += "\n\n" + body
    caption += "\n\nWhat do you think?"

    description_text = f"{summary}\n\n"
    if body:
        description_text += f"{body}\n\n"
    description_text += (
        "Watch the full clip and tell us what you think in the comments.\n\n"
        f"Keywords: {', '.join(topics)}\n\n#shorts #viral"
    )

    return {
        "instagram": {
            "caption": caption,
            "hashtags": hashtags,
        },
        "youtube": {
            "title": hook[:95],
            "title_options": [hook[:95]],
            "description": description_text,
            "tags": ["shorts", "trending", "viral video"] + topics[:12],
            "keywords": topics,
        },
        "content_summary": summary,
        "topics": topics,
    }


# --------------------------------------------------------------------------
# main entry
# --------------------------------------------------------------------------
def metadata_path(cfg: Config, clip: Path) -> Path:
    digest = hashlib.sha1(str(clip).encode("utf-8")).hexdigest()[:10]
    return cfg.metadata_dir / f"{clip.stem}_{digest}.json"


def generate_for_clip(cfg: Config, clip: Path, description, force: bool = False) -> dict[str, Any]:
    path = metadata_path(cfg, clip)
    if path.exists() and not force:
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            cached["source"] = "cache"
            return cached
        except json.JSONDecodeError:
            pass

    source_text = description
    if isinstance(description, dict):
        source_text = description.get("text") or ""

    system = SYSTEM_PROMPT
    user = build_user_prompt(description, cfg, clip.name)
    try:
        raw = generate_json(system, user)
        content = normalise(raw, cfg)
        content["generated_by"] = "ai"
    except AIError as exc:
        content = fallback_content(source_text, cfg)
        content["generated_by"] = "fallback-template"
        content["ai_error"] = str(exc)[:400]

    content["clip_name"] = clip.name
    content["source_description"] = source_text
    if isinstance(description, dict) and description.get("title"):
        content["source_title"] = description["title"]
    content["generated_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(content, indent=2, ensure_ascii=False), encoding="utf-8")
    content["source"] = "generated"
    return content
