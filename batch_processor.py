"""
Batch Processor — Bulk Lyric Video Pipeline
=============================================
Processes all MP3 files in input_songs/ with matching lyrics.

Safeguards:
  1. Pre-flight validation: pairs mp3↔txt and shows a report before starting
  2. Lock files: prevents duplicate processing by parallel workers
  3. Atomic writes: lyrics.json written to temp file, then renamed
  4. Per-song error logs: output_song/<song>/error.log
  5. Live progress dashboard: output_song/progress.json
  6. CSV benchmarking: output_song/benchmark.csv with per-step timings
  7. Done folder: completed mp3+txt moved to done/
"""

import os
import csv
import time
import json
import shutil
import traceback
from pathlib import Path
from concurrent.futures import as_completed
import argparse

# Configuration
INPUT_FOLDER = Path("input_songs")
GROUND_TRUTH_FOLDER = Path("ground_truth_lyrics")
OUTPUT_FOLDER = Path("output_song")
DONE_FOLDER = Path("done")
PROGRESS_FILE = OUTPUT_FOLDER / "progress.json"
BENCHMARK_CSV = OUTPUT_FOLDER / "benchmark.csv"

CSV_HEADERS = [
    "Song Name", "Status", "Extract Lyrics", "Punctuation",
    "NeMo Alignment", "Render Video", "Total Time",
    "Failed At", "Error", "Timestamp"
]


# ─────────────────────────────────────────────────
# 1. PRE-FLIGHT VALIDATION
# ─────────────────────────────────────────────────

def _find_ground_truth(song_path):
    """Find matching ground truth lyrics file for a song. Returns Path or None."""
    song_path = Path(song_path)
    
    exact = GROUND_TRUTH_FOLDER / f"{song_path.name}.txt"
    if exact.exists():
        return exact
    
    stem = GROUND_TRUTH_FOLDER / f"{song_path.stem}.txt"
    if stem.exists():
        return stem
    
    for txt_file in GROUND_TRUTH_FOLDER.glob("*.txt"):
        if song_path.stem[:20] in txt_file.name:
            return txt_file
    
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
# 2. LOCK FILES
# ─────────────────────────────────────────────────

def _acquire_lock(song_name):
    """Create a lock file. Returns True if acquired, False if already locked."""
    lock_file = OUTPUT_FOLDER / song_name / ".processing"
    song_dir = OUTPUT_FOLDER / song_name
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
    lock_file = OUTPUT_FOLDER / song_name / ".processing"
    if lock_file.exists():
        lock_file.unlink()


# ─────────────────────────────────────────────────
# 3. CSV BENCHMARKING
# ─────────────────────────────────────────────────

def _init_csv():
    """Create CSV file with headers if it doesn't exist."""
    BENCHMARK_CSV.parent.mkdir(parents=True, exist_ok=True)
    if not BENCHMARK_CSV.exists():
        with open(BENCHMARK_CSV, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)


def _write_csv_row(song_name, result_info, total_duration):
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
        song_name,
        "✅ SUCCESS" if result_info["status"] == "success" else "❌ FAILED",
        _fmt(timings.get("extract_lyrics", "")),
        _fmt(timings.get("punctuation", "")),
        _fmt(timings.get("nemo_align", "")),
        _fmt(timings.get("render", "")),
        _fmt(total_duration),
        result_info.get("failed_at", ""),
        result_info.get("error", ""),
        time.strftime('%Y-%m-%d %H:%M:%S')
    ]
    
    with open(BENCHMARK_CSV, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(row)


# ─────────────────────────────────────────────────
# 4. FILE MANAGEMENT (done folder)
# ─────────────────────────────────────────────────

def _move_to_done(mp3_path, txt_path):
    """Move completed mp3+txt files to done/ folder."""
    DONE_FOLDER.mkdir(parents=True, exist_ok=True)
    
    mp3_path = Path(mp3_path)
    txt_path = Path(txt_path)
    
    try:
        if mp3_path.exists():
            dest = DONE_FOLDER / mp3_path.name
            shutil.move(str(mp3_path), str(dest))
            print(f"  📦 Moved {mp3_path.name} → done/")
        
        if txt_path.exists():
            dest = DONE_FOLDER / txt_path.name
            shutil.move(str(txt_path), str(dest))
            print(f"  📦 Moved {txt_path.name} → done/")
    except Exception as e:
        print(f"  ⚠️  Could not move files to done/: {e}")


# ─────────────────────────────────────────────────
# 5. PROGRESS DASHBOARD
# ─────────────────────────────────────────────────

def _update_progress(total, done, failed, in_progress, batch_start):
    """Write a live progress.json for monitoring."""
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

def process_single_song(mp3_path_str, txt_path_str, renderer="ffmpeg", nemo_client=None, render_semaphore=None):
    """
    Process a single song with all safeguards.
    Returns dict with song name, status, duration, timings, and failure info.
    """
    from dotenv import load_dotenv
    load_dotenv()
    
    import main as pipeline
    from lyrics_extractor import extract_lyrics_from_text
    
    mp3_path = Path(mp3_path_str)
    txt_path = Path(txt_path_str)
    song_name = mp3_path.stem
    song_dir = OUTPUT_FOLDER / song_name
    start_time = time.time()
    
    # Lock file
    if not _acquire_lock(song_name):
        print(f"  🔒 {song_name} is already being processed. Skipping.")
        return {"song": song_name, "status": "skipped", "duration": 0, "result_info": None}
    
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
            render_semaphore=render_semaphore
        )
        
        # Handle old returns (None) gracefully
        if result_info is None:
            result_info = {"status": "failed", "failed_at": "Unknown", "error": "Pipeline returned None", "timings": {}}
        
        duration = time.time() - start_time
        
        # Write CSV row
        _write_csv_row(song_name, result_info, duration)
        
        # Clear error log on success
        if result_info["status"] == "success":
            error_log = song_dir / "error.log"
            if error_log.exists():
                error_log.unlink()
        
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
        
        # Write CSV row for failure
        fail_info = {"status": "failed", "failed_at": "Exception", "error": error_msg, "timings": {}}
        _write_csv_row(song_name, fail_info, duration)
        
        print(f"\n>>> ERROR: {song_name}: {error_msg}")
        print(f"    Details: {error_log}")
        return {"song": song_name, "status": "failed", "duration": round(duration, 1), "result_info": fail_info}
    
    finally:
        _release_lock(song_name)


# ─────────────────────────────────────────────────
# 7. BATCH ORCHESTRATOR
# ─────────────────────────────────────────────────

def _is_completed(song_name):
    """Check if a song has already been fully processed (has .mp4 output)."""
    song_dir = OUTPUT_FOLDER / song_name
    if not song_dir.exists():
        return False
    return len(list(song_dir.glob("*.mp4"))) > 0


def process_batch(max_workers=1, retry_failed=True, renderer="ffmpeg", max_render_workers=None):
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
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    if max_render_workers is None:
        max_render_workers = min(max_workers, 6)
    
    print(f"\n{'='*60}")
    print(f"       BATCH PROCESSOR (Optimized Pipeline)")
    print(f"       Workers: {max_workers} | Render limit: {max_render_workers} | Renderer: {renderer}")
    print(f"{'='*60}")

    INPUT_FOLDER.mkdir(exist_ok=True)
    GROUND_TRUTH_FOLDER.mkdir(exist_ok=True)
    OUTPUT_FOLDER.mkdir(exist_ok=True)

    # Initialize CSV
    _init_csv()

    # Pre-flight validation
    pairs, no_lyrics = validate_pairs()
    
    if not pairs:
        print("No valid MP3↔TXT pairs found. Nothing to process.")
        return

    # Filter out completed songs
    to_process = []
    skipped = 0
    for mp3_path, txt_path in pairs:
        song_name = mp3_path.stem
        if _is_completed(song_name):
            skipped += 1
        else:
            to_process.append((mp3_path, txt_path))
    
    total = len(pairs)
    remaining = len(to_process)
    
    print(f"Matched pairs: {total}")
    print(f"Already completed: {skipped} (skipped)")
    print(f"To process: {remaining}")
    
    if no_lyrics:
        print(f"⚠️  Songs without lyrics ({len(no_lyrics)}) will be skipped")
    
    if remaining == 0:
        print("\nAll songs already processed! Nothing to do.")
        return
    
    est_per_song = 4.5
    est_parallel = remaining * est_per_song / max_workers
    print(f"\nEstimated time: ~{est_parallel:.0f} min ({est_parallel/60:.1f} hours) with {max_workers} workers")
    print(f"Benchmark CSV: {BENCHMARK_CSV}")
    print(f"Started at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"\n{'='*60}\n")
    
    results = {"success": 0, "failed": 0, "skipped": 0}
    batch_start = time.time()
    
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
        # The semaphore is passed down and acquired only during the render step,
        # so alignment (NeMo) runs freely on all workers
        return process_single_song(mp3_path_str, txt_path_str, renderer, nemo_client, render_semaphore=render_semaphore)
    
    try:
        if max_workers == 1:
            # Sequential mode
            nemo_client = None
            if nemo_server:
                nemo_client = nemo_server.create_client("sequential")
            
            for i, (mp3_path, txt_path) in enumerate(to_process, 1):
                print(f"\n>>> [{i}/{remaining}] Processing: {mp3_path.name}")
                print(f"    Lyrics: {txt_path.name}")
                
                result = process_single_song(str(mp3_path), str(txt_path), renderer=renderer, nemo_client=nemo_client, render_semaphore=None)
                
                if result["status"] == "success":
                    results["success"] += 1
                    _move_to_done(mp3_path, txt_path)
                elif result["status"] == "skipped":
                    results["skipped"] += 1
                else:
                    results["failed"] += 1
                
                _update_progress(remaining, results["success"], results["failed"], 1, batch_start)
                
                elapsed = time.time() - batch_start
                songs_done = results["success"] + results["failed"] + results["skipped"]
                avg_time = elapsed / max(songs_done, 1)
                eta = avg_time * (remaining - songs_done)
                print(f"\n>>> Progress: {songs_done}/{remaining} | ✅ {results['success']} ❌ {results['failed']} | ETA: {eta/60:.0f} min")
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
                            print(f"\n>>> [{i}/{remaining}] ✅ {song_name} ({result['duration']:.0f}s)")
                        elif result["status"] == "skipped":
                            results["skipped"] += 1
                            print(f"\n>>> [{i}/{remaining}] 🔒 {song_name} (skipped)")
                        else:
                            results["failed"] += 1
                            failed_at = result.get("result_info", {}).get("failed_at", "Unknown")
                            print(f"\n>>> [{i}/{remaining}] ❌ {song_name} (failed at: {failed_at})")
                            
                    except Exception as e:
                        results["failed"] += 1
                        fail_info = {"status": "failed", "failed_at": "Exception", "error": str(e), "timings": {}}
                        _write_csv_row(song_name, fail_info, 0)
                        print(f"\n>>> [{i}/{remaining}] ❌ {song_name}: {e}")
                    
                    in_prog = remaining - results["success"] - results["failed"] - results["skipped"]
                    _update_progress(remaining, results["success"], results["failed"], min(in_prog, max_workers), batch_start)
                    
                    elapsed = time.time() - batch_start
                    songs_done = results["success"] + results["failed"] + results["skipped"]
                    if songs_done > 0:
                        avg_time = elapsed / songs_done
                        eta = avg_time * (remaining - songs_done) / max_workers
                        print(f"    Progress: {songs_done}/{remaining} | ✅ {results['success']} ❌ {results['failed']} | ETA: {eta/60:.0f} min")
    
    finally:
        # ── Shutdown NeMo server ──
        if nemo_server:
            try:
                nemo_server.stop()
            except Exception as e:
                print(f"⚠️  Error stopping NeMo server: {e}")
    
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
    print(f"  📦 Completed files moved to: {DONE_FOLDER}/")
    print(f"{'='*60}")
    
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
            f"── TOTAL ({results['success']} songs)",
            f"✅ {results['success']} ❌ {results['failed']}",
            "", "", "", "",
            _fmt_secs(total_time),
            "", "",
            time.strftime('%Y-%m-%d %H:%M:%S')
        ]
        with open(BENCHMARK_CSV, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(summary_row)
    except Exception:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch Process Lyric Videos (NeMo Alignment)")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers (default: 1)")
    parser.add_argument("--max-render-workers", type=int, default=None,
                        help="Max concurrent FFmpeg renders (default: min(workers, 6))")
    parser.add_argument("--no-retry", action="store_true", help="Don't retry previously failed songs")
    parser.add_argument("--ffmpeg", action="store_true", default=True, help="Use FFmpeg renderer (default)")
    parser.add_argument("--remotion", action="store_true", help="Use Remotion renderer")
    args = parser.parse_args()
    
    renderer = "remotion" if args.remotion else "ffmpeg"
    
    process_batch(
        max_workers=args.workers,
        retry_failed=not args.no_retry,
        renderer=renderer,
        max_render_workers=args.max_render_workers
    )

