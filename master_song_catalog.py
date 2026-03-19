"""
master_song_catalog.py
======================
Lookup helpers for Bhajan_Master.xlsx so downstream steps can recover
human-facing metadata from Song IDs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re

import openpyxl


MASTER_EXCEL = (
    Path(__file__).parent.parent
    / "Step 1- Suno Excel File Creator"
    / "Bhajan_Master.xlsx"
)


def _normalize(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


@lru_cache(maxsize=1)
def _load_rows() -> list[dict]:
    if not MASTER_EXCEL.exists():
        return []

    wb = openpyxl.load_workbook(MASTER_EXCEL, read_only=True, data_only=True)
    rows: list[dict] = []
    try:
        for ws in wb.worksheets:
            iterator = ws.iter_rows(values_only=True)
            headers = next(iterator, None)
            if not headers:
                continue
            normalized_headers = [_normalize(h) for h in headers]
            for row in iterator:
                row_dict = {
                    normalized_headers[idx]: _normalize(value)
                    for idx, value in enumerate(row)
                    if idx < len(normalized_headers) and normalized_headers[idx]
                }
                row_dict["Worksheet"] = ws.title
                rows.append(row_dict)
    finally:
        wb.close()
    return rows


def lookup_master_song(song_key: str) -> dict:
    """Find a song row by Song ID first, then by Title."""
    key = _normalize(song_key)
    if not key:
        return {}

    key_lower = key.lower()
    rows = _load_rows()

    for row in rows:
        if _normalize(row.get("Song ID")).lower() == key_lower:
            return dict(row)

    for row in rows:
        if _normalize(row.get("YouTube Title")).lower() == key_lower:
            return dict(row)

    for row in rows:
        if _normalize(row.get("Title")).lower() == key_lower:
            return dict(row)

    return {}


def sanitize_path_component(value: str, fallback: str = "General") -> str:
    text = _normalize(value) or fallback
    text = re.sub(r'[<>:"/\\\\|?*]+', " ", text)
    text = " ".join(text.split()).strip(". ")
    return text or fallback


def workspace_for_song(song_key: str, fallback: str = "General") -> str:
    row = lookup_master_song(song_key)
    worksheet = row.get("Worksheet", "") if row else ""
    return sanitize_path_component(worksheet, fallback=fallback)


def song_output_relpath(song_key: str) -> Path:
    return Path(workspace_for_song(song_key)) / sanitize_path_component(song_key, fallback="song")
