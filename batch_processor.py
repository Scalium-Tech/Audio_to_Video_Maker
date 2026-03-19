"""
Batch Processor — Bulk Lyric Video Pipeline
=============================================
Processes all MP3 files in input_songs/ with matching lyrics.

Safeguards:
  1. Pre-flight validation: pairs mp3↔txt and shows a report before starting
  2. Pre-flight audio validation: ffprobe checks each MP3 for corruption
  3. Duplicate detection: hash-based dedup of identical MP3s
  4. Audio normalization: ffmpeg loudnorm pre-processing
  5. Lock files: prevents duplicate processing by parallel workers
  6. Atomic writes: lyrics.json written to temp file, then renamed
  7. Per-song error logs: output_song/<song>/error.log
  8. Live progress dashboard: dashboard_data/progress.json
  9. CSV benchmarking: dashboard_data/benchmark.csv with per-step timings
 10. Done folder: completed mp3+txt moved to done/
 11. Per-song in-place retry (3 attempts within a batch)
 12. Rate-limit observability warnings
 13. Dry-run mode (--dry-run)
 14. Memory-aware worker scaling
 15. Queue prioritization (partial songs first)
 16. Stage pipelining (--pipeline mode)
"""

import os
import csv
import time
import json
import shutil
import subprocess
import traceback
import hashlib
import sys
from datetime import datetime, date
from pathlib import Path
from concurrent.futures import as_completed
import argparse
import threading
from dotenv import load_dotenv
load_dotenv()

PIPELINE_ROOT = Path(__file__).resolve().parent.parent
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from dashboard_paths import BENCHMARK_CSV, PROGRESS_FILE, ensure_dashboard_data_dir
from master_song_catalog import song_output_relpath

try:
    from song_state import set_stage as _set_stage, add_error as _add_error
    _HAS_SONG_STATE = True
except ImportError:
    _HAS_SONG_STATE = False

try:
    from failure_evidence import save_error_log, save_error_context
    _HAS_EVIDENCE = True
except ImportError:
    _HAS_EVIDENCE = False

# ─────────────────────────────────────────────────
# 0. CONFIG LOADER
# ─────────────────────────────────────────────────


def _load_config():
    """Load pipeline settings from config.yaml, fall back to defaults."""
    defaults = {
        "retry": {"per_song_max_attempts": 3, "per_song_backoff_seconds": 30,
                  "stale_lock_hours": 2, "batch_max_retries": 3},
        "disk": {"min_free_gb": 5.0},
        "pipeline": {"est_minutes_per_song": 4.5},
        "upload": {"rclone_timeout": 300, "rclone_retries": 3, "rclone_transfers": 1},
    }
    try:
        import yaml
        cfg_path = Path(__file__).parent / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f) or {}
            # Merge sections
            for section in defaults:
                if section in cfg:
                    defaults[section].update(cfg[section])
            return defaults
    except Exception:
        pass
    return defaults

_CONFIG = _load_config()

# Configuration
INPUT_FOLDER = Path("input_songs")
GROUND_TRUTH_FOLDER = Path("ground_truth_lyrics")
OUTPUT_FOLDER = Path("output_song")
DONE_FOLDER = Path("done")
DELIVERY_FOLDER = Path.home() / "Desktop" / "Bhajan Video Complete"

# Google Drive rclone config (now loaded from .env)
GDRIVE_REMOTE = os.environ.get("GDRIVE_REMOTE", "bhajan_drive")
GDRIVE_FOLDER = os.environ.get("GDRIVE_FOLDER", "Bhajan Video Complete")

CSV_HEADERS = [
    "Run ID", "Song Name", "Status",
    "Audio Dur (s)", "MP3 Size (MB)",
    "Extract Lyrics", "Punctuation", "NeMo Alignment", "Render Video",
    "MP4 Size (MB)", "GDrive Upload",
    "Total Time", "Retry Count",
    "Failed At", "Error", "Timestamp"
]

# Retry config
PER_SONG_MAX_ATTEMPTS = _CONFIG["retry"]["per_song_max_attempts"]
PER_SONG_BACKOFF = _CONFIG["retry"]["per_song_backoff_seconds"]
STALE_LOCK_HOURS = _CONFIG["retry"]["stale_lock_hours"]


def _song_dir(song_name):
    return OUTPUT_FOLDER / song_output_relpath(song_name)


def _iter_song_dirs():
    if not OUTPUT_FOLDER.exists():
        return []
    song_dirs = []
    for d in OUTPUT_FOLDER.rglob("*"):
        if not d.is_dir() or d == OUTPUT_FOLDER:
            continue
        try:
            if any(child.is_dir() for child in d.iterdir()):
                continue
        except Exception:
            continue
        song_dirs.append(d)
    return song_dirs

class _ConfigWatcher(threading.Thread):
    """Background thread to hot-reload config.yaml."""
    def __init__(self, config_path):
        super().__init__(daemon=True)
        self.config_path = Path(config_path)
        self.last_mtime = 0
        self.running = True
        
    def run(self):
        while self.running:
            try:
                if self.config_path.exists():
                    mtime = self.config_path.stat().st_mtime
                    if self.last_mtime > 0 and mtime > self.last_mtime:
                        global _CONFIG, PER_SONG_MAX_ATTEMPTS, PER_SONG_BACKOFF, STALE_LOCK_HOURS
                        print("\n  ♻️  config.yaml changed! Hot-reloading...")
                        _CONFIG = _load_config()
                        PER_SONG_MAX_ATTEMPTS = _CONFIG["retry"]["per_song_max_attempts"]
                        PER_SONG_BACKOFF = _CONFIG["retry"]["per_song_backoff_seconds"]
                        STALE_LOCK_HOURS = _CONFIG["retry"]["stale_lock_hours"]
                    self.last_mtime = mtime
            except Exception:
                pass
            time.sleep(10)
    
    def stop(self):
        self.running = False



# ─────────────────────────────────────────────────
# 1. PRE-FLIGHT VALIDATION
# ─────────────────────────────────────────────────

def _find_ground_truth(song_path):
    """Find matching ground truth lyrics file for a song. Returns Path or None."""
    song_path = Path(song_path)
    
    # 1. Check ground_truth_lyrics/ folder (original location)
    exact = GROUND_TRUTH_FOLDER / f"{song_path.name}.txt"
    if exact.exists():
        return exact
    
    stem = GROUND_TRUTH_FOLDER / f"{song_path.stem}.txt"
    if stem.exists():
        return stem
    
    for txt_file in GROUND_TRUTH_FOLDER.glob("*.txt"):
        if song_path.stem[:20] in txt_file.name:
            return txt_file
    
    # 2. Check alongside the mp3 in input_songs/ (new songs have txt paired with mp3)
    alongside = song_path.parent / f"{song_path.stem}.txt"
    if alongside.exists():
        return alongside
    
    return None


def validate_pairs():
    """
    Scan input_songs/ and ground_truth_lyrics/, pair them up,
    and return a validated list of (mp3_path, txt_path) tuples.
    """
    song_files = sorted(INPUT_FOLDER.glob("*.mp3"))
    txt_files = set(GROUND_TRUTH_FOLDER.glob("*.txt"))
    
    pairs = []
    no_lyrics = []
    matched_txts = set()
    
    for mp3 in song_files:
        txt = _find_ground_truth(mp3)
        if txt:
            pairs.append((mp3, txt))
            matched_txts.add(txt)
        else:
            no_lyrics.append(mp3)
    
    orphan_txts = txt_files - matched_txts
    
    print(f"\n{'─'*60}")
    print(f"  PRE-FLIGHT VALIDATION")
    print(f"{'─'*60}")
    print(f"  MP3 files found: {len(song_files)}")
    print(f"  TXT files found: {len(txt_files)}")
    print(f"  ✅ Matched pairs: {len(pairs)}")
    
    if no_lyrics:
        print(f"  ❌ MP3 without lyrics: {len(no_lyrics)}")
        for mp3 in no_lyrics[:5]:
            print(f"     - {mp3.name}")
        if len(no_lyrics) > 5:
            print(f"     ... and {len(no_lyrics)-5} more")
    
    if orphan_txts:
        print(f"  ⚠️  Orphan TXT files: {len(orphan_txts)}")
        for txt in list(orphan_txts)[:3]:
            print(f"     - {txt.name}")
    
    # Check for duplicate matching
    txt_to_mp3 = {}
    for mp3, txt in pairs:
        if txt in txt_to_mp3:
            print(f"  🔴 DUPLICATE: {mp3.name} and {txt_to_mp3[txt].name} both match → {txt.name}")
        txt_to_mp3[txt] = mp3
    
    print(f"{'─'*60}\n")
    
    return pairs, no_lyrics


# ─────────────────────────────────────────────────
# 1B. PRE-FLIGHT AUDIO VALIDATION
# ─────────────────────────────────────────────────

def _validate_audio_files(pairs):
    """
    Run ffprobe on each MP3 to catch corrupt/unreadable files BEFORE processing.
    Returns (valid_pairs, rejected_count).
    """
    print(f"\n{'─'*60}")
    print(f"  AUDIO VALIDATION (ffprobe)")
    print(f"{'─'*60}")
    
    valid = []
    rejected = 0
    durations = {}   # {mp3_stem: duration_seconds}

    for mp3_path, txt_path in pairs:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration", "-of", "csv=p=0", str(mp3_path)],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                print(f"  ❌ {mp3_path.name}: ffprobe error — {result.stderr[:100]}")
                rejected += 1
                continue

            duration_str = result.stdout.strip()
            if not duration_str:
                print(f"  ❌ {mp3_path.name}: no duration (empty/corrupt file)")
                rejected += 1
                continue

            duration = float(duration_str)
            if duration < 5.0:
                print(f"  ❌ {mp3_path.name}: too short ({duration:.1f}s)")
                rejected += 1
                continue

            durations[mp3_path.stem] = duration
            valid.append((mp3_path, txt_path))
        except FileNotFoundError:
            print(f"  ⚠️  ffprobe not found! Skipping audio validation.")
            return pairs, 0, {}   # Can't validate, keep all
        except Exception as e:
            print(f"  ❌ {mp3_path.name}: {e}")
            rejected += 1

    if rejected > 0:
        print(f"  ⚠️  Rejected {rejected} file(s)")
    else:
        print(f"  ✅ All {len(valid)} files OK")
    print(f"{'─'*60}\n")

    return valid, rejected, durations


# ─────────────────────────────────────────────────
# 1C. DUPLICATE DETECTION
# ─────────────────────────────────────────────────

def _detect_duplicates(pairs):
    """
    Hash-based duplicate detection. Hashes the first 1MB of each MP3.
    Returns (unique_pairs, duplicate_count).
    """
    print(f"\n{'─'*60}")
    print(f"  DUPLICATE DETECTION")
    print(f"{'─'*60}")
    
    seen_hashes = {}  # hash -> first mp3 name
    unique = []
    dupes = 0
    
    for mp3_path, txt_path in pairs:
        try:
            h = hashlib.sha256()
            with open(mp3_path, 'rb') as f:
                h.update(f.read(1024 * 1024))  # First 1MB
            file_hash = h.hexdigest()[:16]
            
            if file_hash in seen_hashes:
                print(f"  ⚠️  DUPLICATE: {mp3_path.name} ≈ {seen_hashes[file_hash]} (skipping)")
                dupes += 1
            else:
                seen_hashes[file_hash] = mp3_path.name
                unique.append((mp3_path, txt_path))
        except Exception as e:
            print(f"  ⚠️  Can't hash {mp3_path.name}: {e} (keeping)")
            unique.append((mp3_path, txt_path))
    
    if dupes > 0:
        print(f"  ⚠️  Found {dupes} duplicate(s), skipped")
    else:
        print(f"  ✅ No duplicates")
    print(f"{'─'*60}\n")
    
    return unique, dupes


# ─────────────────────────────────────────────────
# 1D. AUDIO NORMALIZATION
# ─────────────────────────────────────────────────

def _normalize_audio(pairs):
    """
    Normalize audio volume using ffmpeg loudnorm filter.
    Normalizes in-place (replaces original MP3) to -16 LUFS.
    Returns count of normalized files.
    """
    print(f"\n{'─'*60}")
    print(f"  AUDIO NORMALIZATION (loudnorm → -16 LUFS)")
    print(f"{'─'*60}")
    
    normalized = 0
    skipped = 0
    
    for mp3_path, _ in pairs:
        try:
            # Check if already normalized (marker file)
            marker = mp3_path.parent / f".{mp3_path.stem}.normalized"
            if marker.exists():
                skipped += 1
                continue
            
            probe = subprocess.check_output(
                [
                    "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                    "-of", "csv=p=0", str(mp3_path)
                ],
                text=True,
                timeout=15
            ).strip()
            duration = float(probe)
            fade_start = max(0.0, duration - 2.0)

            audio_filter = (
                "highpass=f=80,"
                "acompressor=threshold=-20dB:ratio=3:attack=5:release=50,"
                "loudnorm=I=-16:TP=-1.5:LRA=11,"
                f"afade=t=out:st={fade_start:.1f}:d=2"
            )

            tmp_path = mp3_path.with_suffix('.norm.mp3')
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(mp3_path),
                    "-af", audio_filter,
                    "-ar", "44100", "-b:a", "192k",
                    str(tmp_path)
                ],
                capture_output=True, text=True, timeout=60
            )
            
            if result.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 0:
                tmp_path.replace(mp3_path)  # Atomic in-place replace
                marker.write_text("normalized")
                normalized += 1
            else:
                if tmp_path.exists():
                    tmp_path.unlink()
        except FileNotFoundError:
            print(f"  ⚠️  ffmpeg not found! Skipping normalization.")
            return 0
        except subprocess.TimeoutExpired:
            print(f"  ⚠️  Timeout normalizing {mp3_path.name}, skipping")
            tmp_path = mp3_path.with_suffix('.norm.mp3')
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception as e:
            print(f"  ⚠️  Error normalizing {mp3_path.name}: {e}")
    
    print(f"  ✅ Normalized: {normalized} | Already done: {skipped} | Total: {len(pairs)}")
    print(f"{'─'*60}\n")
    
    return normalized


# ─────────────────────────────────────────────────
# 1E. QUEUE PRIORITIZATION
# ─────────────────────────────────────────────────

def _sort_by_priority(to_process):
    """
    Sort queue so partially-done songs (lyrics.json exists, no MP4) come first.
    They only need rendering, so they finish faster → early output.
    """
    def priority_key(pair):
        song_name = pair[0].stem
        song_dir = _song_dir(song_name)
        has_lyrics = (song_dir / "lyrics.json").exists()
        has_video = any(song_dir.glob("*.mp4")) if song_dir.exists() else False
        
        if has_lyrics and not has_video:
            return 0  # Partial — highest priority
        elif not has_lyrics:
            return 1  # Fresh — normal priority
        else:
            return 2  # Shouldn't happen (completed), lowest
    
    return sorted(to_process, key=priority_key)


# ─────────────────────────────────────────────────
# 1F. MEMORY-AWARE SCALING
# ─────────────────────────────────────────────────

def _check_memory_pressure(threshold_pct=80):
    """
    Check if system memory usage exceeds threshold.
    Returns True if memory is OK, False if under pressure.
    """
    try:
        import psutil
        mem = psutil.virtual_memory()
        if mem.percent > threshold_pct:
            print(f"  ⚠️  MEMORY PRESSURE: {mem.percent:.0f}% used ({mem.available / (1024**3):.1f} GB free). Pausing for 30s...", flush=True)
            import time as _time
            _time.sleep(30)
            return False
        return True
    except ImportError:
        # Fallback: try vm_stat (macOS)
        try:
            result = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
            # Parse free pages
            for line in result.stdout.split('\n'):
                if 'Pages free' in line:
                    free_pages = int(line.split(':')[1].strip().rstrip('.'))
                    free_gb = (free_pages * 4096) / (1024**3)
                    if free_gb < 2.0:  # Less than 2GB free
                        print(f"  ⚠️  LOW MEMORY: ~{free_gb:.1f} GB free. Pausing for 30s...", flush=True)
                        import time as _time
                        _time.sleep(30)
                        return False
        except Exception:
            pass
    return True


# ─────────────────────────────────────────────────
# 1G. STALE LOCK FILE CLEANUP
# ─────────────────────────────────────────────────

def _cleanup_stale_locks():
    """
    Sweep all output_song/ sub-dirs for .processing lock files
    older than STALE_LOCK_HOURS and remove them.
    """
    if not OUTPUT_FOLDER.exists():
        return
    
    threshold = STALE_LOCK_HOURS * 3600
    cleaned = 0
    for lock_file in OUTPUT_FOLDER.rglob(".processing"):
        try:
            age = time.time() - lock_file.stat().st_mtime
            if age > threshold:
                lock_file.unlink()
                cleaned += 1
        except Exception:
            pass
    
    if cleaned > 0:
        print(f"  🗑️  Cleaned {cleaned} stale lock file(s) (older than {STALE_LOCK_HOURS}h)")


# ─────────────────────────────────────────────────
# 2. LOCK FILES
# ─────────────────────────────────────────────────

def _acquire_lock(song_name):
    """Create a lock file. Returns True if acquired, False if already locked."""
    lock_file = _song_dir(song_name) / ".processing"
    song_dir = _song_dir(song_name)
    song_dir.mkdir(parents=True, exist_ok=True)
    
    if lock_file.exists():
        age = time.time() - lock_file.stat().st_mtime
        if age > 1800:  # 30 min stale
            print(f"  ⚠️  Stale lock found ({age/60:.0f} min old). Overriding.")
            lock_file.unlink()
        else:
            return False
    
    lock_file.write_text(f"locked at {time.strftime('%Y-%m-%d %H:%M:%S')} by pid {os.getpid()}")
    return True


def _release_lock(song_name):
    """Remove the lock file."""
    lock_file = _song_dir(song_name) / ".processing"
    if lock_file.exists():
        lock_file.unlink()


# ─────────────────────────────────────────────────
# 3. CSV BENCHMARKING
# ─────────────────────────────────────────────────

def _init_csv():
    """Create benchmark CSV. Archives old file if headers don't match new format."""
    ensure_dashboard_data_dir()
    BENCHMARK_CSV.parent.mkdir(parents=True, exist_ok=True)
    if BENCHMARK_CSV.exists():
        try:
            with open(BENCHMARK_CSV, 'r', newline='', encoding='utf-8') as f:
                existing_headers = next(csv.reader(f), [])
            if existing_headers != CSV_HEADERS:
                archive = BENCHMARK_CSV.parent / f"benchmark_archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                BENCHMARK_CSV.rename(archive)
                print(f"  📦 Old benchmark archived → {archive.name}")
        except Exception:
            pass
    if not BENCHMARK_CSV.exists():
        with open(BENCHMARK_CSV, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)


def _write_csv_row(song_name, result_info, total_duration,
                   run_id="", audio_dur=0, mp3_mb=0.0, mp4_mb=0.0, retry_count=1):
    """Append a row to the benchmark CSV. Times as Xm Ys."""
    timings = result_info.get("timings", {})

    def _fmt(val):
        """Format seconds as 'Xm Ys' or 'Xs'."""
        if val == "" or val is None:
            return ""
        secs = float(val)
        if secs < 60:
            return f"{secs:.0f}s"
        return f"{int(secs // 60)}m {int(secs % 60)}s"

    row = [
        run_id,
        song_name,
        "✅ SUCCESS" if result_info["status"] == "success" else "❌ FAILED",
        f"{audio_dur:.0f}" if audio_dur else "",
        f"{mp3_mb:.2f}" if mp3_mb else "",
        _fmt(timings.get("extract_lyrics", "")),
        _fmt(timings.get("punctuation", "")),
        _fmt(timings.get("nemo_align", "")),
        _fmt(timings.get("render", "")),
        f"{mp4_mb:.2f}" if mp4_mb else "",
        "",   # GDrive Upload — filled in by upload_queue after upload
        _fmt(total_duration),
        str(retry_count),
        result_info.get("failed_at", ""),
        result_info.get("error", ""),
        time.strftime('%Y-%m-%d %H:%M:%S')
    ]

    with open(BENCHMARK_CSV, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(row)


# ─────────────────────────────────────────────────
# 4. FILE MANAGEMENT (done folder + delivery)
# ─────────────────────────────────────────────────

def _move_to_done(mp3_path, txt_path):
    """Move completed mp3+txt files to done/ folder. Skips if already there."""
    DONE_FOLDER.mkdir(parents=True, exist_ok=True)
    
    mp3_path = Path(mp3_path)
    txt_path = Path(txt_path)
    
    try:
        if mp3_path.exists():
            dest = DONE_FOLDER / mp3_path.name
            if dest.exists():
                mp3_path.unlink()  # Already in done/, just remove from input
                print(f"  📦 {mp3_path.name} already in done/ (removed from input)")
            else:
                shutil.move(str(mp3_path), str(dest))
                print(f"  📦 Moved {mp3_path.name} → done/")
        
        if txt_path.exists():
            dest = DONE_FOLDER / txt_path.name
            if dest.exists():
                txt_path.unlink()  # Already in done/, just remove from input
                print(f"  📦 {txt_path.name} already in done/ (removed from input)")
            else:
                shutil.move(str(txt_path), str(dest))
                print(f"  📦 Moved {txt_path.name} → done/")
    except Exception as e:
        print(f"  ⚠️  Could not move files to done/: {e}")


def _move_video_to_delivery(song_name):
    """
    Upload completed video folder to Google Drive via rclone, then delete locally.
    Falls back to local delivery folder if rclone fails.
    Returns True if Drive upload succeeded (safe to delete source files).
    """
    song_dir = _song_dir(song_name)
    
    if not song_dir.exists() or not list(song_dir.glob("*.mp4")):
        print(f"  ⚠️  No .mp4 found in {song_name}/, skipping delivery")
        return False
    
    # Try rclone upload to Google Drive
    gdrive_dest = f"{GDRIVE_REMOTE}:{GDRIVE_FOLDER}/{song_output_relpath(song_name).as_posix()}"
    try:
        result = subprocess.run(
            [
                "rclone", "move",
                str(song_dir),
                gdrive_dest,
                "--delete-empty-src-dirs",
                "--transfers", "4",
                "--checkers", "2",
                "-v"
            ],
            capture_output=True, text=True, timeout=600  # 10 min timeout per song
        )
        
        if result.returncode == 0:
            # Force-delete entire output folder — already on Drive
            if song_dir.exists():
                shutil.rmtree(str(song_dir), ignore_errors=True)
            print(f"  ☁️  Uploaded {song_name}/ → GDrive/{GDRIVE_FOLDER}/ (local copy deleted)")
            return True
        else:
            print(f"  ⚠️  rclone upload failed for {song_name}: {result.stderr[:200]}")
            # Fallback: keep locally in delivery folder
            _fallback_local_delivery(song_name, song_dir)
            return False
    except subprocess.TimeoutExpired:
        print(f"  ⚠️  rclone upload timed out for {song_name}, keeping locally")
        _fallback_local_delivery(song_name, song_dir)
        return False
    except FileNotFoundError:
        print(f"  ⚠️  rclone not found! Install with: brew install rclone")
        _fallback_local_delivery(song_name, song_dir)
        return False
    except Exception as e:
        print(f"  ⚠️  Upload error for {song_name}: {e}")
        _fallback_local_delivery(song_name, song_dir)
        return False


def _fallback_local_delivery(song_name, song_dir):
    """Fallback: move to local delivery folder if rclone upload fails."""
    DELIVERY_FOLDER.mkdir(parents=True, exist_ok=True)
    dest = DELIVERY_FOLDER / song_output_relpath(song_name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(song_dir), str(dest))
        print(f"  📦 Saved locally → {DELIVERY_FOLDER.name}/{song_name}/")
    except Exception as e:
        print(f"  ❌ Could not save {song_name} anywhere: {e}")





def _cleanup_failed_outputs():
    """
    Remove output_song/ folders that have no .mp4 (leftover from failed runs).
    Frees disk space from partial/failed processing attempts.
    """
    if not OUTPUT_FOLDER.exists():
        return
    
    cleaned = 0
    freed_bytes = 0
    for song_dir in sorted(_iter_song_dirs()):
        # Skip if it has an MP4 (successfully rendered)
        if list(song_dir.glob("*.mp4")):
            continue
        # Skip if currently being processed
        if (song_dir / ".processing").exists():
            age = time.time() - (song_dir / ".processing").stat().st_mtime
            if age < 1800:  # less than 30 min old
                continue
        # Calculate size before deleting
        try:
            for f in song_dir.rglob("*"):
                if f.is_file():
                    freed_bytes += f.stat().st_size
            shutil.rmtree(str(song_dir), ignore_errors=True)
            cleaned += 1
        except Exception:
            pass
    
    if cleaned > 0:
        print(f"  🗑️  Cleaned {cleaned} failed output folders ({freed_bytes / 1024 / 1024:.0f} MB freed)")


def _deliver_completed_videos():
    """
    Scan output_song/ for folders with an MP4 and try to deliver them.
    Useful for picking up videos from interrupted sessions.
    """
    to_deliver = []
    if not OUTPUT_FOLDER.exists():
        return
    
    for d in _iter_song_dirs():
        if list(d.glob("*.mp4")):
            to_deliver.append(d.relative_to(OUTPUT_FOLDER).as_posix())
    
    if to_deliver:
        print(f"\n📦 Found {len(to_deliver)} completed videos pending delivery. Syncing...")
        for rel_song_path in to_deliver:
            _move_video_to_delivery(Path(rel_song_path).name)
        print(f"✅ Startup sync complete.\n")


def _cleanup_caches():
    """
    Clear safe system caches to free disk space after batch processing.
    Only clears caches that are safe to remove (pip, Homebrew, npm).
    """
    freed_total = 0
    
    # 1. pip cache
    try:
        result = subprocess.run(
            ["pip", "cache", "purge"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            print(f"  🗑️  Cleared pip cache")
    except Exception:
        pass
    
    # 2. Homebrew cache
    try:
        cache_dir = Path.home() / "Library" / "Caches" / "Homebrew"
        if cache_dir.exists():
            size = sum(f.stat().st_size for f in cache_dir.rglob("*") if f.is_file())
            if size > 50 * 1024 * 1024:  # Only if > 50MB
                result = subprocess.run(
                    ["brew", "cleanup", "--prune=all", "-s"],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    freed_total += size
                    print(f"  🗑️  Cleared Homebrew cache ({size / 1024 / 1024:.0f} MB)")
    except Exception:
        pass
    
    # 3. npm cache
    try:
        result = subprocess.run(
            ["npm", "cache", "clean", "--force"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            print(f"  🗑️  Cleared npm cache")
    except Exception:
        pass
    
    # 4. Clean __pycache__ in project directory
    try:
        for pycache in Path(".").rglob("__pycache__"):
            if "venv" not in str(pycache):
                shutil.rmtree(str(pycache), ignore_errors=True)
    except Exception:
        pass


def _check_disk_threshold(min_gb=5.0):
    """
    Check if free disk space is below threshold and trigger emergency cleanup.
    Default threshold is 5GB.
    """
    try:
        usage = shutil.disk_usage("/")
        free_gb = usage.free / (1024**3)
        
        if free_gb < min_gb:
            print(f"\n⚠️  LOW DISK SPACE WARNING: {free_gb:.2f} GB remaining.")
            print(f"   Triggering emergency cleanup...")
            _cleanup_failed_outputs()
            _cleanup_caches()
            
            # Check again
            new_usage = shutil.disk_usage("/")
            new_free_gb = new_usage.free / (1024**3)
            print(f"   Cleanup complete. New free space: {new_free_gb:.2f} GB\n")
            return True
    except Exception as e:
        print(f"⚠️  Error checking disk space: {e}")
    return False


# ─────────────────────────────────────────────────
# 5. PROGRESS DASHBOARD
# ─────────────────────────────────────────────────

def _update_progress(total, done, failed, in_progress, batch_start):
    """Write a live progress.json for monitoring."""
    ensure_dashboard_data_dir()
    elapsed = time.time() - batch_start
    avg_per_song = elapsed / max(done + failed, 1)
    remaining = total - done - failed
    
    progress = {
        "total": total,
        "completed": done,
        "failed": failed,
        "in_progress": in_progress,
        "remaining": remaining,
        "elapsed_minutes": round(elapsed / 60, 1),
        "eta_minutes": round(avg_per_song * remaining / 60, 1),
        "avg_per_song_minutes": round(avg_per_song / 60, 1),
        "updated_at": time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(".tmp")
    with open(tmp, 'w') as f:
        json.dump(progress, f, indent=2)
    tmp.rename(PROGRESS_FILE)


# ─────────────────────────────────────────────────
# 6. PROCESS SINGLE SONG
# ─────────────────────────────────────────────────

def process_single_song(mp3_path_str, txt_path_str, renderer="ffmpeg", nemo_client=None,
                        render_semaphore=None, preview_mode=False,
                        run_id="", audio_dur=0.0, mp3_mb=0.0, retry_count=1):
    """
    Process a single song with all safeguards.
    Returns dict with song name, status, duration, timings, and failure info.
    """
    import main as pipeline
    from lyrics_extractor import extract_lyrics_from_text

    mp3_path = Path(mp3_path_str)
    txt_path = Path(txt_path_str)
    song_name = mp3_path.stem
    song_dir = _song_dir(song_name)
    start_time = time.time()

    # Lock file
    if not _acquire_lock(song_name):
        print(f"  🔒 {song_name} is already being processed. Skipping.")
        return {"song": song_name, "status": "skipped", "duration": 0, "result_info": None}

    # Mark render_started in song_state
    if _HAS_SONG_STATE:
        try:
            _set_stage(song_name, "render_started")
        except Exception:
            pass

    try:
        # Read lyrics from the exact matched txt file
        raw_text = open(txt_path, "r", encoding="utf-8").read()
        ground_truth_text = extract_lyrics_from_text(raw_text)

        if not ground_truth_text or not ground_truth_text.strip():
            raise ValueError(f"Empty lyrics extracted from {txt_path.name}")

        # Execute pipeline — returns per-step timing
        result_info = pipeline.main(
            str(mp3_path),
            ground_truth_text=ground_truth_text,
            renderer=renderer,
            nemo_client=nemo_client,
            render_semaphore=render_semaphore,
            preview_mode=preview_mode,
            output_dir=str(OUTPUT_FOLDER)
        )

        # Handle old returns (None) gracefully
        if result_info is None:
            result_info = {"status": "failed", "failed_at": "Unknown", "error": "Pipeline returned None", "timings": {}}

        duration = time.time() - start_time

        # Measure MP4 output size (before upload removes it)
        mp4_mb = 0.0
        if result_info.get("status") == "success":
            mp4_files = list(song_dir.glob("*.mp4"))
            if mp4_files:
                mp4_mb = mp4_files[0].stat().st_size / (1024 * 1024)

        # Write CSV row
        _write_csv_row(song_name, result_info, duration,
                       run_id=run_id, audio_dur=audio_dur, mp3_mb=mp3_mb,
                       mp4_mb=mp4_mb, retry_count=retry_count)

        if result_info["status"] == "success":
            error_log = song_dir / "error.log"
            if error_log.exists():
                error_log.unlink()
            try:
                dest_txt = song_dir / f"{song_name}.txt"
                shutil.copy2(str(txt_path), str(dest_txt))
            except Exception as e:
                print(f"  ⚠️  Failed to copy original txt for {song_name}: {e}")
            if _HAS_SONG_STATE:
                try:
                    _set_stage(song_name, "render_done")
                except Exception:
                    pass
        elif result_info["status"] == "failed" and _HAS_EVIDENCE:
            try:
                save_error_context(
                    song_name, result_info.get("failed_at", "pipeline"), result_info.get("error", ""),
                    last_known_state=result_info.get("failed_at", ""),
                    extra={"timings": result_info.get("timings", {}), "retry_count": retry_count},
                )
            except Exception:
                pass
            if _HAS_SONG_STATE:
                try:
                    _add_error(song_name, result_info.get("failed_at", "pipeline"), result_info.get("error", ""))
                except Exception:
                    pass

        return {
            "song": song_name,
            "status": result_info["status"],
            "duration": round(duration, 1),
            "result_info": result_info
        }

    except Exception as e:
        duration = time.time() - start_time
        error_msg = f"{type(e).__name__}: {e}"

        # Save error log
        song_dir.mkdir(parents=True, exist_ok=True)
        error_log = song_dir / "error.log"
        with open(error_log, 'w') as f:
            f.write(f"Song: {song_name}\n")
            f.write(f"MP3: {mp3_path}\n")
            f.write(f"TXT: {txt_path}\n")
            f.write(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Duration: {duration:.1f}s\n")
            f.write(f"\nError: {error_msg}\n\n")
            f.write(traceback.format_exc())

        fail_info = {"status": "failed", "failed_at": "Exception", "error": error_msg, "timings": {}}
        _write_csv_row(song_name, fail_info, duration,
                       run_id=run_id, audio_dur=audio_dur, mp3_mb=mp3_mb,
                       mp4_mb=0.0, retry_count=retry_count)

        print(f"\n>>> ERROR: {song_name}: {error_msg}")
        print(f"    Details: {error_log}")
        # Save structured error context JSON
        if _HAS_EVIDENCE:
            try:
                save_error_context(
                    song_name, fail_info.get("failed_at", "render"), error_msg,
                    last_known_state=fail_info.get("failed_at", ""),
                    extra={"timings": fail_info.get("timings", {}), "retry_count": retry_count}
                )
            except Exception:
                pass
        # Record error in song_state
        if _HAS_SONG_STATE:
            try:
                _add_error(song_name, fail_info.get("failed_at", "Unknown"), error_msg)
            except Exception:
                pass
        return {"song": song_name, "status": "failed", "duration": round(duration, 1), "result_info": fail_info}

    finally:
        _release_lock(song_name)


def _process_with_retry(mp3_path_str, txt_path_str, renderer="ffmpeg", nemo_client=None,
                        render_semaphore=None, preview_mode=False,
                        run_id="", audio_dur=0.0, mp3_mb=0.0):
    """
    Wrapper that retries process_single_song up to PER_SONG_MAX_ATTEMPTS times
    for transient failures (rate limits, timeouts, connection errors).
    """
    for attempt in range(1, PER_SONG_MAX_ATTEMPTS + 1):
        result = process_single_song(
            mp3_path_str, txt_path_str, renderer, nemo_client,
            render_semaphore, preview_mode=preview_mode,
            run_id=run_id, audio_dur=audio_dur, mp3_mb=mp3_mb,
            retry_count=attempt
        )

        # Success or skip → return immediately
        if result["status"] in ("success", "skipped"):
            return result

        # On failure: check if it's likely transient
        error_msg = (result.get("result_info", {}) or {}).get("error", "")
        is_transient = any(kw in error_msg.lower() for kw in [
            "429", "rate limit", "timeout", "timed out", "connection",
            "server error", "502", "503", "504", "resource exhausted"
        ])

        if not is_transient or attempt >= PER_SONG_MAX_ATTEMPTS:
            return result  # Permanent failure or last attempt

        song_name = Path(mp3_path_str).stem
        print(f"  🔄 Transient failure for {song_name} (attempt {attempt}/{PER_SONG_MAX_ATTEMPTS}). Retrying in {PER_SONG_BACKOFF}s...", flush=True)
        time.sleep(PER_SONG_BACKOFF)

    return result


# ─────────────────────────────────────────────────
# 7. BATCH ORCHESTRATOR
# ─────────────────────────────────────────────────

def _is_completed(song_name):
    """Check if a song has already been fully processed (has .mp4 output)."""
    song_dir = _song_dir(song_name)
    if not song_dir.exists():
        return False
    return len(list(song_dir.glob("*.mp4"))) > 0


def _is_partially_done(song_name):
    """Check if a song has lyrics.json but no .mp4 (alignment done, render needed)."""
    song_dir = _song_dir(song_name)
    if not song_dir.exists():
        return False
    has_lyrics = (song_dir / "lyrics.json").exists()
    has_video = len(list(song_dir.glob("*.mp4"))) > 0
    return has_lyrics and not has_video


def _validate_rclone_connection():
    """
    Mandatory pre-flight check for Google Drive connection.
    Stops the pipeline if the token is expired or rclone is misconfigured.
    """
    print(f"\n{'─'*60}")
    print(f"  RCLONE CHECK — Validating Google Drive connection...")
    print(f"{'─'*60}")
    
    try:
        # Try a lightweight operation
        result = subprocess.run(
            ["rclone", "about", f"{GDRIVE_REMOTE}:", "--timeout", "30s"],
            capture_output=True, text=True
        )
        
        if result.returncode == 0:
            print(f"  ✅ Drive connection verified.")
            print(f"{'─'*60}\n")
            return True
        else:
            error = result.stderr.lower()
            if "expired" in error or "token" in error or "unauthorized" in error:
                print(f"\n❌ ERROR: Google Drive token is EXPIRED.")
            elif "remote not found" in error:
                print(f"\n❌ ERROR: Rclone remote '{GDRIVE_REMOTE}' NOT found.")
            else:
                print(f"\n❌ ERROR: Rclone connection failed: {result.stderr[:200]}")
            
            print(f"\n💡 ACTION REQUIRED:")
            print(f"   Run: rclone config reconnect {GDRIVE_REMOTE}:")
            print(f"   (This will open a browser to refresh your login)\n")
            return False
            
    except Exception as e:
        print(f"  ⚠️  Error during rclone check: {e}")
        return False


def _sync_done_from_drive():
    """
    Check Google Drive for already-uploaded song folders.
    Move corresponding source files (mp3+txt) from input_songs/ to done/.
    This prevents re-processing songs that are already on Drive.
    """
    DONE_FOLDER.mkdir(parents=True, exist_ok=True)
    
    print(f"\n{'─'*60}")
    print(f"  DRIVE SYNC — Checking already-uploaded songs...")
    print(f"{'─'*60}")
    
    try:
        result = subprocess.run(
            ["rclone", "lsf", f"{GDRIVE_REMOTE}:{GDRIVE_FOLDER}/", "--dirs-only"],
            capture_output=True, text=True, timeout=60
        )
        
        if result.returncode != 0:
            print(f"  ⚠️  rclone lsf failed: {result.stderr[:200]}")
            print(f"  Skipping drive sync, continuing with normal processing.\n")
            return
        
        # Parse folder names from Drive (they end with /)
        drive_folders = set()
        for line in result.stdout.strip().split("\n"):
            folder_name = line.strip().rstrip("/")
            if folder_name:
                drive_folders.add(folder_name)
        
        if not drive_folders:
            print(f"  No folders found on Drive. Skipping sync.\n")
            return
        
        print(f"  Found {len(drive_folders)} folders on Drive.")
        
        moved_count = 0
        for song_name in sorted(drive_folders):
            # Check if source files still exist in input_songs/
            mp3_path = INPUT_FOLDER / f"{song_name}.mp3"
            txt_path_input = INPUT_FOLDER / f"{song_name}.txt"
            txt_path_gt = GROUND_TRUTH_FOLDER / f"{song_name}.txt"
            txt_path_gt2 = GROUND_TRUTH_FOLDER / f"{song_name}.mp3.txt"
            
            moved_any = False
            
            if mp3_path.exists():
                dest = DONE_FOLDER / mp3_path.name
                if dest.exists():
                    mp3_path.unlink()  # Already in done/, just remove from input
                else:
                    shutil.move(str(mp3_path), str(dest))
                moved_any = True
            
            for txt_path in [txt_path_input, txt_path_gt, txt_path_gt2]:
                if txt_path.exists():
                    dest = DONE_FOLDER / txt_path.name
                    if dest.exists():
                        txt_path.unlink()  # Already in done/, just remove from input
                    else:
                        shutil.move(str(txt_path), str(dest))
                    moved_any = True
            
            # Also clean up local output_song/ folder if it exists
            local_output = _song_dir(song_name)
            if local_output.exists():
                shutil.rmtree(str(local_output), ignore_errors=True)
            
            if moved_any:
                moved_count += 1
        
        if moved_count > 0:
            print(f"  ✅ Moved {moved_count} already-uploaded songs to done/")
        else:
            print(f"  ✅ All Drive songs already in done/. No moves needed.")
        print(f"{'─'*60}\n")
        
    except subprocess.TimeoutExpired:
        print(f"  ⚠️  rclone timed out. Skipping drive sync.\n")
    except FileNotFoundError:
        print(f"  ⚠️  rclone not found. Skipping drive sync.\n")
    except Exception as e:
        print(f"  ⚠️  Drive sync error: {e}. Continuing.\n")


def process_batch(max_workers=1, retry_failed=True, renderer="ffmpeg", max_render_workers=None, dry_run=False, skip_normalize=False, use_process_pool=False, pipeline_mode=False, preview_mode=False, start_at=None, use_date_folders=True, multi_machine=False, start_health=True):
    """
    Batch process all songs with full safeguards.
    
    Two-stage pipeline with shared NeMo model:
      1. Shared NeMo server loads model once, serves all workers
      2. Jobs queued with max_workers, but FFmpeg renders limited by max_render_workers
    
    Args:
        max_workers: Total parallel job slots (default: 1)
        retry_failed: Retry previously failed songs
        renderer: "ffmpeg" or "remotion"
        max_render_workers: Max concurrent FFmpeg encodes (default: min(max_workers, 6))
        dry_run: If True, validate everything but don't process
        skip_normalize: If True, skip audio normalization
        use_process_pool: If True, use ProcessPoolExecutor instead of threads
        pipeline_mode: If True, use two-stage pipeline (alignment → render)
        preview_mode: If True, render short previews
        start_at: HH:MM string to schedule run time
        multi_machine: Enable file-based queueing across machines
        start_health: Start the /status HTTP server
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    global OUTPUT_FOLDER
    OUTPUT_FOLDER = Path("output_song")
        
    if start_at:
        try:
            target_time = datetime.strptime(start_at, "%H:%M").time()
            now = datetime.now()
            target = datetime.combine(now.date(), target_time)
            if target < now:
                import datetime as dt
                target += dt.timedelta(days=1)
            
            wait_seconds = (target - now).total_seconds()
            print(f"\n⏳ Scheduled to start at {start_at} ({target.strftime('%Y-%m-%d %H:%M:%S')})")
            print(f"💤 Sleeping for {wait_seconds/60:.1f} minutes...")
            time.sleep(wait_seconds)
            print("\n⏰ Time reached! Starting batch process.")
        except Exception as e:
            print(f"⚠️  Failed to parse --start-at time '{start_at}'. Starting immediately. {e}")

    # Start Config Watcher
    config_watcher = _ConfigWatcher(Path(__file__).parent / "config.yaml")
    config_watcher.start()
    
    # Start Health Server
    health_server = None
    if start_health:
        try:
            from health_server import HealthServer
            health_server = HealthServer()
            health_server.start()
        except Exception as e:
            print(f"⚠️  Health server error: {e}")
            
    # Init Multi-machine queue
    mq = None
    if multi_machine:
        try:
            from machine_queue import MachineQueue
            mq = MachineQueue()
            print(f"  🔗 Multi-machine queue enabled. Host: {mq.hostname}")
        except Exception as e:
            print(f"⚠️  Multi-machine queue error: {e}")
    
    if max_render_workers is None:
        max_render_workers = min(max_workers, 6)
    
    print(f"\n{'='*60}")
    print(f"       BATCH PROCESSOR (Optimized Pipeline)")
    print(f"       Workers: {max_workers} | Render limit: {max_render_workers} | Renderer: {renderer}")
    if pipeline_mode:
        print(f"       🔀 PIPELINE MODE — alignment and rendering overlap")
    if use_process_pool:
        print(f"       🔄 PROCESS POOL — true multiprocessing")
    if dry_run:
        print(f"       ⚠️  DRY RUN — no songs will be processed")
    print(f"{'='*60}")

    INPUT_FOLDER.mkdir(exist_ok=True)
    GROUND_TRUTH_FOLDER.mkdir(exist_ok=True)
    OUTPUT_FOLDER.mkdir(exist_ok=True)
    DONE_FOLDER.mkdir(exist_ok=True)

    # ── Startup cleanup: stale lock files ──
    _cleanup_stale_locks()

    # 1. Validate Rclone Connection (Pre-flight)
    if not _validate_rclone_connection():
        print("❌ Critical: Rclone connection check failed. Stopping for safety.")
        return

    # 2. Deliver any orphaned videos from previous sessions first ──
    _deliver_completed_videos()

    # ── Sync: check Drive for already-uploaded songs, move to done/ ──
    _sync_done_from_drive()

    # ── Clean up failed output folders (no MP4) to free disk space ──
    _cleanup_failed_outputs()

    # Initialize CSV
    _init_csv()

    # Pre-flight validation
    pairs, no_lyrics = validate_pairs()
    
    if not pairs:
        print("No valid MP3↔TXT pairs found. Nothing to process.")
        return

    # ── Pre-flight audio validation ──
    pairs, rejected_count, audio_durations = _validate_audio_files(pairs)

    # ── Build MP3 size lookup (MB per song stem) ──
    mp3_sizes = {p.stem: p.stat().st_size / (1024 * 1024) for p, _ in pairs if p.exists()}

    # ── Duplicate detection ──
    pairs, dupe_count = _detect_duplicates(pairs)

    # ── Audio normalization ──
    if not skip_normalize and not dry_run:
        _normalize_audio(pairs)

    # Filter out completed songs, categorize partial completions
    to_process = []
    skipped = 0
    partial = 0
    for mp3_path, txt_path in pairs:
        song_name = mp3_path.stem
        if _is_completed(song_name):
            skipped += 1
        elif mq and not mq.claim(song_name):
            skipped += 1
            print(f"  ⏭️ {song_name} claimed by another machine")
        else:
            if _is_partially_done(song_name):
                partial += 1
            to_process.append((mp3_path, txt_path))
    
    # ── Sort queue: partial songs first ──
    to_process = _sort_by_priority(to_process)
    
    total = len(pairs)
    remaining = len(to_process)
    
    print(f"\n{'─'*60}")
    print(f"  CHECKPOINT SUMMARY")
    print(f"{'─'*60}")
    print(f"  Matched pairs: {total}")
    print(f"  ✅ Fully completed: {skipped} (skipped)")
    if partial > 0:
        print(f"  🔄 Partially done (lyrics ready, needs render): {partial}")
    print(f"  🆕 To process: {remaining - partial}")
    print(f"  📋 Total queue: {remaining}")
    print(f"{'─'*60}")
    
    if no_lyrics:
        print(f"⚠️  Songs without lyrics ({len(no_lyrics)}) will be skipped")
    
    if remaining == 0:
        print("\nAll songs already processed! Nothing to do.")
        return
    
    est_per_song = 4.5
    est_partial_song = 2.0  # Partial songs only need rendering
    est_time = (partial * est_partial_song + (remaining - partial) * est_per_song) / max_workers
    print(f"\nEstimated time: ~{est_time:.0f} min ({est_time/60:.1f} hours) with {max_workers} workers")
    print(f"Benchmark CSV: {BENCHMARK_CSV}")
    print(f"Started at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"\n{'='*60}\n")
    
    # ── Dry-run mode: validate only, don't process ──
    if dry_run:
        print(f"{'─'*60}")
        print(f"  DRY RUN SUMMARY")
        print(f"{'─'*60}")
        print(f"  ✅ MP3↔TXT pairs: {len(pairs)} valid")
        if rejected_count > 0:
            print(f"  ⚠️  Rejected audio files: {rejected_count}")
        print(f"  📋 Queue: {remaining} songs to process")
        print(f"  ⏱️  ETA: ~{est_time:.0f} min with {max_workers} workers")
        # Check API keys
        try:
            from gemini_utils import _pool_manager
            for pool_name in ["alignment", "image", "punctuation", "default"]:
                count = _pool_manager.get_key_count(pool_name)
                print(f"  🔑 Gemini [{pool_name}]: {count} key(s)")
        except Exception:
            pass
        print(f"  ☁️  rclone: verified (see check above)")
        print(f"{'─'*60}")
        print(f"\n✅ Dry run complete. Everything looks good!")
        return
    
    results = {"success": 0, "failed": 0, "skipped": 0}
    rate_limit_failures = 0  # Track rate-limit failures for adaptive warning
    batch_start = time.time()
    RUN_ID = datetime.now().strftime('%Y%m%d_%H%M%S')
    print(f"  🆔 Run ID: {RUN_ID}")

    # ── Start background upload queue ──
    from upload_queue import UploadQueue

    upload_queue = UploadQueue(
        output_folder=OUTPUT_FOLDER,
        delivery_folder=DELIVERY_FOLDER,
        gdrive_remote=GDRIVE_REMOTE,
        gdrive_folder=GDRIVE_FOLDER,
        benchmark_csv=BENCHMARK_CSV
    )
    upload_queue.start()
    
    # ── Start shared NeMo model server ──
    nemo_server = None
    nemo_clients = {}
    
    if max_workers > 1:
        try:
            from nemo_server import NemoModelServer
            print("Starting shared NeMo model server...", flush=True)
            nemo_server = NemoModelServer()
            nemo_server.start()
            print("✅ NeMo server ready — model loaded once, shared across all workers\n", flush=True)
        except Exception as e:
            print(f"⚠️  NeMo server failed to start: {e}")
            print("   Falling back to per-worker model loading.\n")
            nemo_server = None
    
    # ── Render semaphore: limits concurrent FFmpeg encodes ──
    render_semaphore = threading.Semaphore(max_render_workers)
    
    def _throttled_process(mp3_path_str, txt_path_str, renderer, nemo_client):
        """Wrapper that passes render semaphore to throttle only FFmpeg renders."""
        stem = Path(mp3_path_str).stem
        return _process_with_retry(
            mp3_path_str, txt_path_str, renderer, nemo_client,
            render_semaphore=render_semaphore, preview_mode=preview_mode,
            run_id=RUN_ID,
            audio_dur=audio_durations.get(stem, 0.0),
            mp3_mb=mp3_sizes.get(stem, 0.0)
        )
    
    try:
        if max_workers == 1:
            # Sequential mode
            nemo_client = None
            if nemo_server:
                nemo_client = nemo_server.create_client("sequential")
            
            for i, (mp3_path, txt_path) in enumerate(to_process, 1):
                print(f"\n>>> [{i}/{remaining}] Processing: {mp3_path.name}")
                print(f"    Lyrics: {txt_path.name}")
                
                result = _process_with_retry(
                    str(mp3_path), str(txt_path), renderer=renderer,
                    nemo_client=nemo_client, render_semaphore=None, preview_mode=preview_mode,
                    run_id=RUN_ID,
                    audio_dur=audio_durations.get(mp3_path.stem, 0.0),
                    mp3_mb=mp3_sizes.get(mp3_path.stem, 0.0)
                )
                
                if result["status"] == "success":
                    results["success"] += 1
                    _move_to_done(mp3_path, txt_path)
                    upload_queue.enqueue(mp3_path.stem)
                    if mq: mq.mark_done(mp3_path.stem)
                elif result["status"] == "skipped":
                    results["skipped"] += 1
                    if mq: mq.mark_done(mp3_path.stem)
                else:
                    results["failed"] += 1
                    if mq: mq.release(mp3_path.stem)
                    # Track rate-limit failures
                    err = (result.get("result_info", {}) or {}).get("error", "")
                    if "429" in err or "rate limit" in err.lower():
                        rate_limit_failures += 1
                
                songs_done = results["success"] + results["failed"] + results["skipped"]
                elapsed = time.time() - batch_start
                avg_time = elapsed / max(songs_done, 1)
                eta = avg_time * (remaining - songs_done)
                print(f"\n>>> Progress: {songs_done}/{remaining} | ✅ {results['success']} ❌ {results['failed']} | ETA: {eta/60:.0f} min")
                
                # Check disk space and memory after each song
                _check_disk_threshold()
                _check_memory_pressure()
        else:
            # Parallel mode with ThreadPoolExecutor + render semaphore
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for mp3_path, txt_path in to_process:
                    # Create a per-worker NeMo client if server is running
                    nemo_client = None
                    if nemo_server:
                        worker_key = f"worker_{mp3_path.stem}"
                        nemo_client = nemo_server.create_client(worker_key)
                    
                    future = executor.submit(
                        _throttled_process,
                        str(mp3_path), str(txt_path), renderer, nemo_client
                    )
                    futures[future] = (mp3_path, txt_path)
                
                for i, future in enumerate(as_completed(futures), 1):
                    mp3_path, txt_path = futures[future]
                    song_name = mp3_path.stem
                    try:
                        result = future.result(timeout=1800)
                        
                        if result["status"] == "success":
                            results["success"] += 1
                            _move_to_done(mp3_path, txt_path)
                            upload_queue.enqueue(song_name)
                            if mq: mq.mark_done(song_name)
                            print(f"\n>>> [{i}/{remaining}] ✅ {song_name} ({result['duration']:.0f}s)")
                        elif result["status"] == "skipped":
                            results["skipped"] += 1
                            if mq: mq.mark_done(song_name)
                            print(f"\n>>> [{i}/{remaining}] 🔒 {song_name} (skipped)")
                        else:
                            results["failed"] += 1
                            if mq: mq.release(song_name)
                            failed_at = result.get("result_info", {}).get("failed_at", "Unknown")
                            print(f"\n>>> [{i}/{remaining}] ❌ {song_name} (failed at: {failed_at})")
                            # Track rate-limit failures
                            err = (result.get("result_info", {}) or {}).get("error", "")
                            if "429" in err or "rate limit" in err.lower():
                                rate_limit_failures += 1
                            
                    except Exception as e:
                        results["failed"] += 1
                        fail_info = {"status": "failed", "failed_at": "Exception", "error": str(e), "timings": {}}
                        _write_csv_row(song_name, fail_info, 0, run_id=RUN_ID)
                        print(f"\n>>> [{i}/{remaining}] ❌ {song_name}: {e}")
                    
                    in_prog = remaining - results["success"] - results["failed"] - results["skipped"]
                    _update_progress(remaining, results["success"], results["failed"], min(in_prog, max_workers), batch_start)
                    
                    songs_done = results["success"] + results["failed"] + results["skipped"]
                    elapsed = time.time() - batch_start
                    if songs_done > 0:
                        avg_time = elapsed / songs_done
                        eta = avg_time * (remaining - songs_done) / max_workers
                        print(f"    Progress: {songs_done}/{remaining} | ✅ {results['success']} ❌ {results['failed']} | ETA: {eta/60:.0f} min")
                    
                    # Periodic health check on NeMo server (every 10 completed songs)
                    if nemo_server and songs_done % 10 == 0 and songs_done > 0:
                        if not nemo_server.ensure_alive():
                            print("  ⚠️  NeMo server unrecoverable. Remaining songs will load model per-worker.", flush=True)
                            nemo_server = None
                    
                    # Check disk space and memory after each worker completes
                    _check_disk_threshold()
                    _check_memory_pressure()
    
    except KeyboardInterrupt:
        print("\n\n⚠️  Process interrupted by user (Ctrl+C). Cleaning up...")
        if mq:
            for mp3_path, _ in to_process:
                mq.release(mp3_path.stem)
    except Exception as e:
        print(f"\n\n🔥 Fatal error in batch processor: {e}")
        traceback.print_exc()
        if mq:
            for mp3_path, _ in to_process:
                mq.release(mp3_path.stem)
    finally:
        print(f"\nFinal pipeline cleanup...", flush=True)
        config_watcher.stop()
        if health_server:
            health_server.stop()
        
        # ── Shutdown NeMo server ──
        if nemo_server:
            try:
                nemo_server.stop()
            except Exception as e:
                print(f"⚠️  Error stopping NeMo server: {e}")
        
        # ── Drain background upload queue ──
        try:
            print("\n⏳ Waiting for background uploads to finish...", flush=True)
            upload_queue.drain()
            upload_queue.stop()
        except Exception as e:
            print(f"⚠️  Error draining upload queue: {e}")
    
    # Final report
    total_time = time.time() - batch_start
    print(f"\n{'='*60}")
    print(f"       BATCH COMPLETE")
    print(f"{'='*60}")
    print(f"  Total processed: {results['success'] + results['failed']}")
    print(f"  ✅ Success: {results['success']}")
    print(f"  ❌ Failed: {results['failed']}")
    if results['skipped'] > 0:
        print(f"  🔒 Skipped: {results['skipped']}")
    print(f"  ⏱️  Total time: {total_time/60:.1f} min ({total_time/3600:.1f} hours)")
    if results['success'] > 0:
        print(f"  📊 Avg per song: {total_time/max(results['success'],1)/60:.1f} min")
    print(f"  📄 Benchmark: {BENCHMARK_CSV}")
    print(f"  📦 Source files moved to: {DONE_FOLDER}/")
    print(f"  🚀 Videos delivered to: {DELIVERY_FOLDER}/")
    
    # ── Token usage summary ──
    try:
        from gemini_utils import get_token_summary
        tokens = get_token_summary()
        grand_total = tokens.get("grand_total_tokens", 0)
        if grand_total > 0:
            print(f"\n  📊 Gemini Token Usage:")
            for pool_name, usage in tokens.get("per_pool", {}).items():
                print(f"     [{pool_name}] {usage['total_tokens']:,} tokens ({usage['calls']} calls)")
            print(f"     TOTAL: {grand_total:,} tokens")
    except Exception:
        pass
    
    # ── Rate-limit observability ──
    if rate_limit_failures > 0:
        total_done = results["success"] + results["failed"]
        pct = (rate_limit_failures / max(total_done, 1)) * 100
        print(f"\n  ⚠️  Rate-limit related failures: {rate_limit_failures} ({pct:.0f}% of failures)")
        if pct > 30:
            print(f"  💡 TIP: Consider reducing --workers or adding more API keys to reduce rate limiting.")
    
    # ── Cache stats ──
    try:
        from gemini_utils import get_cache_stats
        cache_stats = get_cache_stats()
        if cache_stats["hits"] + cache_stats["misses"] > 0:
            print(f"\n  💾 Gemini Cache: {cache_stats['hits']} hits, {cache_stats['misses']} misses ({cache_stats['hit_rate']}% hit rate)")
            print(f"     Cache files: {cache_stats['cache_files']}")
    except Exception:
        pass
    
    print(f"{'='*60}")
    
    # ── Generate HTML batch report ──
    try:
        from report_generator import generate_report
        generate_report(str(BENCHMARK_CSV))
    except Exception as e:
        print(f"  ⚠️  Report generation failed: {e}")
    
    # Run auto-cleanup of caches to ensure disk space remains free
    _cleanup_caches()
    
    if results['failed'] > 0:
        print(f"\n❌ Failed songs (kept in input_songs/ for retry):")
        try:
            with open(BENCHMARK_CSV, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if "FAILED" in row.get("Status", ""):
                        print(f"  - {row['Song Name']}: failed at {row.get('Failed At', '?')} — {row.get('Error', '')[:80]}")
        except Exception:
            pass
        print(f"\nRe-run with: ./start  (failed songs will be retried)")
    
    # Final progress
    progress_data = {
        "total": remaining,
        "completed": results["success"],
        "failed": results["failed"],
        "in_progress": 0,
        "remaining": 0,
        "elapsed_minutes": round(total_time / 60, 1),
        "eta_minutes": 0,
        "status": "COMPLETE",
        "updated_at": time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    tmp = PROGRESS_FILE.with_suffix(".tmp")
    with open(tmp, 'w') as f:
        json.dump(progress_data, f, indent=2)
    tmp.rename(PROGRESS_FILE)
    
    # Add summary row to CSV
    try:
        def _fmt_secs(secs):
            if secs < 60:
                return f"{secs:.0f}s"
            return f"{int(secs // 60)}m {int(secs % 60)}s"
        
        summary_row = [
            RUN_ID,
            f"── TOTAL ({results['success']} songs)",
            f"✅ {results['success']} ❌ {results['failed']}",
            "", "",   # Audio Dur, MP3 Size
            "", "", "", "",   # step timings
            "", "",   # MP4 Size, GDrive Upload
            _fmt_secs(total_time),
            "",   # Retry Count
            "", "",   # Failed At, Error
            time.strftime('%Y-%m-%d %H:%M:%S')
        ]
        with open(BENCHMARK_CSV, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(summary_row)
    except Exception:
        pass

    # Exit codes: 0 = all ok, 1 = all failed, 2 = partial (some ok, some failed)
    if results['failed'] > 0 and results['success'] == 0:
        sys.exit(1)
    elif results['failed'] > 0:
        sys.exit(2)


if __name__ == "__main__":
    ensure_dashboard_data_dir()
    parser = argparse.ArgumentParser(description="Batch Process Lyric Videos (NeMo Alignment)")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers (default: 1)")
    parser.add_argument("--max-render-workers", type=int, default=None,
                        help="Max concurrent FFmpeg renders (default: min(workers, 6))")
    parser.add_argument("--no-retry", action="store_true", help="Don't retry previously failed songs")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint (skip completed, render partial)")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs only, don't process")
    parser.add_argument("--skip-normalize", action="store_true", help="Skip audio normalization")
    parser.add_argument("--process-pool", action="store_true", help="Use ProcessPoolExecutor for true parallelism")
    parser.add_argument("--pipeline", action="store_true", help="Pipeline mode: overlap alignment and rendering")
    parser.add_argument("--preview", action="store_true", help="Preview mode: render only first 15s of each video for QA")
    parser.add_argument("--ffmpeg", action="store_true", default=True, help="Use FFmpeg renderer (default)")
    parser.add_argument("--remotion", action="store_true", help="Use Remotion renderer")
    parser.add_argument("--start-at", type=str, help="Schedule start time (format: HH:MM)")
    parser.add_argument("--multi-machine", action="store_true", help="Enable file-based multi-machine queue tracking")
    parser.add_argument("--no-health", action="store_true", help="Disable the background health check HTTP server")
    
    args = parser.parse_args()
    
    renderer = "remotion" if args.remotion else "ffmpeg"
    
    process_batch(
        max_workers=args.workers,
        retry_failed=not args.no_retry,
        renderer=renderer,
        max_render_workers=args.max_render_workers,
        dry_run=args.dry_run,
        skip_normalize=args.skip_normalize,
        use_process_pool=args.process_pool,
        pipeline_mode=args.pipeline,
        preview_mode=args.preview,
        start_at=args.start_at,
        multi_machine=args.multi_machine,
        start_health=not args.no_health
    )
