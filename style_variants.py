"""
style_variants.py
=================
Deterministic render-style selection for lightweight A/B testing.
"""

from __future__ import annotations

import hashlib
import os


STYLE_VARIANTS = {
    "classic": {
        "key": "classic",
        "label": "Classic Glow",
        "dark_overlay_alpha": 102,
        "line_y_ratio": 0.68,
        "particle_count": 72,
        "progress_bg": (255, 255, 255, 40),
        "progress_fg": (255, 120, 80, 210),
        "chorus_fill_alpha": 64,
        "chorus_outline_alpha": 92,
    },
    "festival": {
        "key": "festival",
        "label": "Festival Spark",
        "dark_overlay_alpha": 92,
        "line_y_ratio": 0.69,
        "particle_count": 96,
        "progress_bg": (255, 245, 220, 52),
        "progress_fg": (255, 185, 70, 220),
        "chorus_fill_alpha": 78,
        "chorus_outline_alpha": 110,
    },
}

_ALIASES = {
    "a": "classic",
    "b": "festival",
    "default": "classic",
}


def _normalize_variant_name(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    return _ALIASES.get(normalized, normalized)


def choose_style_variant(song_name: str, explicit: str | None = None) -> dict:
    requested = _normalize_variant_name(explicit or os.environ.get("LYRICFLOW_STYLE_VARIANT"))
    if requested and requested != "auto" and requested in STYLE_VARIANTS:
        return dict(STYLE_VARIANTS[requested])

    ordered_keys = sorted(STYLE_VARIANTS)
    digest = hashlib.sha1(song_name.encode("utf-8")).hexdigest()
    index = int(digest[:8], 16) % len(ordered_keys)
    return dict(STYLE_VARIANTS[ordered_keys[index]])
