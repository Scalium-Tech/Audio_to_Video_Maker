"""
thumbnail_generator.py
======================
Create a YouTube-ready thumbnail from the generated song background.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from ffmpeg_render import _find_devanagari_font, _get_theme_for_song
from master_song_catalog import lookup_master_song


THUMB_W = 1280
THUMB_H = 720


def _format_title(song_name: str) -> str:
    title = song_name.replace("_", " ").replace("-", " ").strip()
    if not title:
        return "Bhajan"
    return " ".join(part for part in title.split() if part)


def _wrap_title(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return ["Bhajan"]

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textbbox((0, 0), trial, font=font)[2] <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)

    if len(lines) > 3:
        collapsed = textwrap.wrap(text, width=max(10, len(text) // 3))
        return collapsed[:3]
    return lines


def generate_thumbnail(
    song_name: str,
    background_path: str | Path,
    output_path: str | Path,
) -> Path:
    background_path = Path(background_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    master_song = lookup_master_song(song_name)
    display_title = master_song.get("YouTube Title") or master_song.get("Title") or _format_title(song_name)
    theme = _get_theme_for_song(f"{song_name} {display_title}")
    title = display_title

    if background_path.exists():
        base = Image.open(background_path).convert("RGB")
    else:
        base = Image.new("RGB", (THUMB_W, THUMB_H), color=(26, 22, 18))

    base = base.resize((THUMB_W, THUMB_H), Image.LANCZOS)

    # Add a cinematic dark gradient so text stays readable.
    gradient = Image.new("L", (1, THUMB_H))
    for y in range(THUMB_H):
        alpha = int(40 + 180 * (y / THUMB_H))
        gradient.putpixel((0, y), min(alpha, 220))
    gradient = gradient.resize((THUMB_W, THUMB_H))

    dark_overlay = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 0))
    dark_overlay.putalpha(gradient)

    accent = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 0))
    accent_draw = ImageDraw.Draw(accent)
    accent_color = theme["glow"]
    accent_draw.ellipse(
        [-160, -80, 680, 760],
        fill=(accent_color[0], accent_color[1], accent_color[2], 70),
    )
    accent = accent.filter(ImageFilter.GaussianBlur(40))

    canvas = Image.alpha_composite(base.convert("RGBA"), accent)
    canvas = Image.alpha_composite(canvas, dark_overlay)
    draw = ImageDraw.Draw(canvas)

    title_font = _find_devanagari_font(76)
    subtitle_font = _find_devanagari_font(34)
    badge_font = _find_devanagari_font(32)

    lines = _wrap_title(draw, title, title_font, max_width=950)
    line_gap = 18
    line_heights = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=title_font, stroke_width=4)
        line_heights.append(bbox[3] - bbox[1])

    block_height = sum(line_heights) + line_gap * max(0, len(lines) - 1)
    y = THUMB_H - block_height - 140
    x = 92

    for idx, line in enumerate(lines):
        draw.text(
            (x, y),
            line,
            font=title_font,
            fill=theme["normal"],
            stroke_width=4,
            stroke_fill=(0, 0, 0),
        )
        y += line_heights[idx] + line_gap

    subtitle = "Bhakti Bhajan"
    draw.text(
        (x, y + 8),
        subtitle,
        font=subtitle_font,
        fill=(255, 232, 185),
        stroke_width=2,
        stroke_fill=(0, 0, 0),
    )

    badge_haystack = f"{song_name} {display_title}".lower()
    badge_text = next(
        (name.title() for name in ["shiva", "krishna", "ram", "ganesh", "durga"] if name in badge_haystack),
        "Bhajan",
    )
    badge_box = [92, 72, 312, 142]
    badge_fill = (*theme["active"][:3], 220)
    draw.rounded_rectangle(badge_box, radius=22, fill=badge_fill, outline=(255, 255, 255, 110), width=2)
    badge_bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
    badge_w = badge_bbox[2] - badge_bbox[0]
    badge_h = badge_bbox[3] - badge_bbox[1]
    badge_x = badge_box[0] + ((badge_box[2] - badge_box[0] - badge_w) / 2)
    badge_y = badge_box[1] + ((badge_box[3] - badge_box[1] - badge_h) / 2) - 2
    draw.text((badge_x, badge_y), badge_text, font=badge_font, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))

    canvas.convert("RGB").save(output_path, quality=95)
    return output_path
