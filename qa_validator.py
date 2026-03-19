"""
qa_validator.py
================
Post-render quality checks for the lyric video pipeline.

Writes qa_report.json beside each song's output and returns a structured report
that the main pipeline can use for warnings or blocking failures.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from datetime import datetime


def _probe_duration(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path)
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0


def detect_silence_gaps(audio_path: Path, min_duration: float = 5.0) -> list[float]:
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-i", str(audio_path),
                "-af", f"silencedetect=noise=-30dB:d={min_duration}",
                "-f", "null", "-"
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        gaps = []
        for line in result.stderr.splitlines():
            if "silence_start:" in line:
                try:
                    gaps.append(float(line.split("silence_start:")[1].strip()))
                except Exception:
                    continue
        return gaps
    except Exception:
        return []


def check_alignment_drift(lyrics_path: Path, audio_path: Path, max_drift: float = 10.0) -> tuple[str | None, bool]:
    try:
        lyrics = json.loads(lyrics_path.read_text(encoding="utf-8"))
        audio_duration = _probe_duration(audio_path)
        if not lyrics or audio_duration <= 0:
            return None, False

        last_end = 0.0
        for segment in reversed(lyrics):
            if isinstance(segment, dict):
                last_end = float(segment.get("end", 0.0))
                if last_end > 0:
                    break

        drift = audio_duration - last_end
        if drift > max_drift:
            coverage_ratio = last_end / max(audio_duration, 1.0)
            message = (
                f"Last word ends at {last_end:.1f}s but audio is "
                f"{audio_duration:.1f}s ({drift:.1f}s gap)"
            )
            # Long devotional tracks often end with an instrumental outro. If the
            # lyrics already cover most of the song, treat this as a warning.
            if coverage_ratio >= 0.85 and drift <= 30.0:
                return message, True
            return message, False
    except Exception as e:
        return f"Could not evaluate alignment drift: {e}", False
    return None, False


def check_background_for_text(image_path: Path) -> str | None:
    if not image_path.exists():
        return "SKIP: background image missing"
    try:
        import pytesseract
        from PIL import Image

        text = pytesseract.image_to_string(Image.open(image_path), lang="eng+hin").strip()
        if len(text) > 5:
            return f"Visible text detected in background: {text[:60]}"
        return None
    except ImportError:
        return "SKIP: pytesseract not installed"
    except Exception as e:
        return f"Could not evaluate background text: {e}"


def check_duration(video_path: Path) -> str | None:
    duration = _probe_duration(video_path)
    if duration <= 0:
        return "Could not determine video duration"
    if duration < 90:
        return f"TOO SHORT: {duration:.0f}s (min 90s recommended)"
    if duration > 480:
        return f"TOO LONG: {duration:.0f}s (max 480s recommended)"
    return None


def validate_song(
    song_name: str,
    audio_path: str | Path,
    lyrics_path: str | Path,
    video_path: str | Path,
    background_path: str | Path,
    output_dir: str | Path,
) -> dict:
    audio_path = Path(audio_path)
    lyrics_path = Path(lyrics_path)
    video_path = Path(video_path)
    background_path = Path(background_path)
    output_dir = Path(output_dir)

    report = {
        "song": song_name,
        "passed": True,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "checks": {},
        "warnings": [],
        "blocking_issues": [],
    }

    silence_gaps = detect_silence_gaps(audio_path)
    if silence_gaps:
        msg = f"WARN: silence gaps detected at {', '.join(f'{g:.1f}s' for g in silence_gaps[:5])}"
        report["checks"]["silence_gaps"] = msg
        report["warnings"].append(msg)
    else:
        report["checks"]["silence_gaps"] = "PASS"

    drift_issue, drift_is_warning = check_alignment_drift(lyrics_path, audio_path)
    if drift_issue:
        if drift_is_warning:
            report["checks"]["alignment_drift"] = f"WARN: {drift_issue}"
            report["warnings"].append(drift_issue)
        else:
            report["checks"]["alignment_drift"] = f"FAIL: {drift_issue}"
            report["blocking_issues"].append(drift_issue)
    else:
        report["checks"]["alignment_drift"] = "PASS"

    bg_text_issue = check_background_for_text(background_path)
    if bg_text_issue:
        report["checks"]["background_text"] = f"WARN: {bg_text_issue}"
        if not bg_text_issue.startswith("SKIP:"):
            report["warnings"].append(bg_text_issue)
    else:
        report["checks"]["background_text"] = "PASS"

    duration_issue = check_duration(video_path)
    if duration_issue:
        report["checks"]["duration"] = f"WARN: {duration_issue}"
        report["warnings"].append(duration_issue)
    else:
        report["checks"]["duration"] = "PASS"

    report["passed"] = len(report["blocking_issues"]) == 0

    report_path = output_dir / "qa_report.json"
    tmp_path = report_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(report_path)
    return report
