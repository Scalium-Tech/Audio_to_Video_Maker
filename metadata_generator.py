"""
metadata_generator.py
=====================
Create publish-ready metadata for the YouTube upload step.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from channel_router import route_channel
from master_song_catalog import lookup_master_song
from style_variants import choose_style_variant


PUBLISH_SLOTS = [(9, 0), (13, 0), (18, 0)]


def _format_title(song_name: str) -> str:
    title = song_name.replace("_", " ").replace("-", " ").strip()
    return " ".join(title.split()) or "Bhajan"


def _reserved_slots(output_root: Path) -> set[str]:
    slots = set()
    for metadata_file in output_root.rglob("metadata.json"):
        try:
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
            publish_at = data.get("publish_at")
            if publish_at:
                slots.add(publish_at)
        except Exception:
            continue
    return slots


def _next_publish_slot(output_root: Path, now: datetime | None = None) -> str:
    now = now or datetime.now()
    reserved = _reserved_slots(output_root)

    for day_offset in range(0, 30):
        day = (now + timedelta(days=day_offset)).date()
        for hour, minute in PUBLISH_SLOTS:
            slot = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
            if slot <= now + timedelta(minutes=15):
                continue
            slot_str = slot.strftime("%Y-%m-%dT%H:%M:%S")
            if slot_str not in reserved:
                return slot_str

    fallback = now + timedelta(days=1)
    return fallback.strftime("%Y-%m-%dT09:00:00")


def generate_metadata(song_name: str, output_dir: str | Path, qa_report: dict | None = None) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    master_song = lookup_master_song(song_name)
    display_title = master_song.get("YouTube Title") or master_song.get("Title") or _format_title(song_name)
    content_category = master_song.get("Category") or master_song.get("Bucket") or ""
    route = route_channel(f"{song_name} {display_title}")
    style_variant = choose_style_variant(song_name)
    title = display_title
    deity = route["deity"]
    publish_root = output_dir.parent.parent if output_dir.parent.parent.exists() else output_dir.parent
    publish_at = _next_publish_slot(publish_root)

    tags = [
        "bhajan",
        "devotional songs",
        "hindi bhajan",
        "spiritual music",
        route["playlist_title"].lower(),
    ]
    if deity != "general":
        tags.extend([f"{deity} bhajan", deity, f"{deity} devotional"])

    # Preserve order while removing duplicates.
    deduped_tags = list(dict.fromkeys(tags))

    warning_line = ""
    if qa_report and qa_report.get("warnings"):
        warning_line = f"\nQA Notes: {len(qa_report['warnings'])} warning(s) detected during validation."

    metadata = {
        "title": title,
        "description": (
            f"{title}\n\n"
            f"Listen to this devotional bhajan from the {route['channel_name']} pipeline.\n"
            f"Playlist: {route['playlist_title']}\n"
            f"Deity Focus: {deity.title() if deity != 'general' else 'General Devotional'}\n"
            f"{warning_line}".strip()
        ),
        "tags": deduped_tags,
        "playlist": route["playlist_title"],
        "channel_name": route["channel_name"],
        "channel_key": route["channel_key"],
        "deity": deity,
        "song_id": master_song.get("Song ID", song_name),
        "youtube_title": display_title,
        "content_category": content_category,
        "thumbnail_concept": master_song.get("Thumbnail Concept", ""),
        "master_sheet": master_song.get("Worksheet", ""),
        "style_variant": style_variant["key"],
        "style_variant_label": style_variant["label"],
        "publish_at": publish_at,
        "video_path": str(output_dir / f"{song_name}.mp4"),
        "thumbnail_path": str(output_dir / "thumbnail.jpg"),
        "qa_report_path": str(output_dir / "qa_report.json"),
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }

    metadata_path = output_dir / "metadata.json"
    tmp_path = metadata_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(metadata_path)
    return metadata_path
