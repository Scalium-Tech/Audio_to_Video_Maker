"""
compilation_builder.py
======================
Build long-form compilation videos from existing per-song MP4 outputs.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def _probe_duration(video_path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0


def _discover_entries(source_root: Path, deity: str | None = None) -> list[dict]:
    entries = []
    for metadata_file in sorted(source_root.rglob("metadata.json")):
        try:
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        video_path = Path(data.get("video_path", metadata_file.parent / f"{metadata_file.parent.name}.mp4"))
        if not video_path.exists():
            continue

        entry_deity = data.get("deity", "general")
        if deity and entry_deity != deity:
            continue

        entries.append(
            {
                "title": data.get("title", video_path.stem),
                "video_path": video_path.resolve(),
                "duration_sec": _probe_duration(video_path),
                "deity": entry_deity,
                "metadata_path": metadata_file.resolve(),
            }
        )
    return entries


def _select_entries(entries: list[dict], target_minutes: float) -> list[dict]:
    selected = []
    total_sec = 0.0
    target_sec = target_minutes * 60.0

    for entry in entries:
        if entry["duration_sec"] <= 0:
            continue
        if selected and total_sec >= target_sec:
            break
        selected.append(entry)
        total_sec += entry["duration_sec"]
    return selected


def build_compilation(
    source_root: str | Path,
    output_path: str | Path,
    deity: str | None = None,
    target_minutes: float = 60.0,
    title: str | None = None,
) -> Path:
    source_root = Path(source_root)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    entries = _discover_entries(source_root, deity=deity)
    selected = _select_entries(entries, target_minutes=target_minutes)
    if not selected:
        raise RuntimeError("No matching videos found for compilation")

    concat_list_path = output_path.with_suffix(".concat.txt")
    concat_lines = []
    for entry in selected:
        safe_path = entry["video_path"].as_posix().replace("'", "'\\''")
        concat_lines.append(f"file '{safe_path}'")
    concat_list_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_path),
        "-c",
        "copy",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-500:] or "Compilation build failed")

    manifest = {
        "title": title or output_path.stem.replace("_", " ").strip(),
        "deity": deity or "general",
        "target_minutes": target_minutes,
        "actual_duration_sec": round(sum(entry["duration_sec"] for entry in selected), 2),
        "video_count": len(selected),
        "videos": [
            {
                "title": entry["title"],
                "video_path": str(entry["video_path"]),
                "duration_sec": round(entry["duration_sec"], 2),
            }
            for entry in selected
        ],
    }
    manifest_path = output_path.with_suffix(".json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Build a devotional compilation video from existing outputs.")
    parser.add_argument("--source-root", default="output_song", help="Root folder containing per-song output subfolders")
    parser.add_argument("--output", required=True, help="Path to the final compilation MP4")
    parser.add_argument("--deity", default=None, help="Optional deity filter, e.g. shiva or krishna")
    parser.add_argument("--minutes", type=float, default=60.0, help="Approximate target duration in minutes")
    parser.add_argument("--title", default=None, help="Optional compilation title for the sidecar manifest")
    args = parser.parse_args()

    output = build_compilation(
        source_root=args.source_root,
        output_path=args.output,
        deity=args.deity,
        target_minutes=args.minutes,
        title=args.title,
    )
    print(output)


if __name__ == "__main__":
    main()
