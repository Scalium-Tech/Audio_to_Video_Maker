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
"""

import os
import time
import json
import traceback
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import argparse

# Configuration
INPUT_FOLDER = Path("input_songs")
GROUND_TRUTH_FOLDER = Path("ground_truth_lyrics")
OUTPUT_FOLDER = Path("output_song")
PROGRESS_FILE = OUTPUT_FOLDER / "progress.json"


# ─────────────────────────────────────────────────
# 1. PRE-FLIGHT VALIDATION
# ─────────────────────────────────────────────────

def _find_ground_truth(song_path):
    """Find matching ground truth lyrics file for a song. Returns Path or None."""
    song_path = Path(song_path)
    
    # Priority 1: exact match  <song_name>.mp3.txt
    exact = GROUND_TRUTH_FOLDER / f"{song_path.name}.txt"
    if exact.exists():
        return exact
    
    # Priority 2: stem match  <song_name>.txt
    stem = GROUND_TRUTH_FOLDER / f"{song_path.stem}.txt"
    if stem.exists():
        return stem
    
    # Priority 3: fuzzy match (first 20 chars of stem in filename)
    for txt_file in GROUND_TRUTH_FOLDER.glob("*.txt"):
        if song_path.stem[:20] in txt_file.name:
            return txt_file
    
    return None


def validate_pairs():
    """
    Scan input_songs/ and ground_truth_lyrics/, pair them up,
    and return a validated list of (mp3_path, txt_path) tuples.
    
    Prints a clear report showing matches, missing, and orphans.
    """
    song_files = sorted(INPUT_FOLDER.glob("*.mp3"))
    txt_files = set(GROUND_TRUTH_FOLDER.glob("*.txt"))
    
    pairs = []       # (mp3_path, txt_path)
    no_lyrics = []   # mp3 files with no matching txt
    matched_txts = set()
    
    for mp3 in song_files:
        txt = _find_ground_truth(mp3)
        if txt:
            pairs.append((mp3, txt))
            matched_txts.add(txt)
        else:
            no_lyrics.append(mp3)
    
    orphan_txts = txt_files - matched_txts  # txt files with no matching mp3
    
    # Print report
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
    
    # Check for duplicate matching (two mp3s → same txt)
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
        # Check if lock is stale (older than 30 minutes)
        age = time.time() - lock_file.stat().st_mtime
        if age > 1800:  # 30 min
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
# 3. PROGRESS DASHBOARD
# ─────────────────────────────────────────────────

def _update_progress(total, done, failed, in_progress, batch_start):
    """Write a live progress.json for monitoring."""
    elapsed = time.time() - batch_start
    avg_per_song = elapsed / max(done + failed, 1)
    remaining = total - done - failed
    eta_seconds = avg_per_song * remaining
    
    progress = {
        "total": total,
        "completed": done,
        "failed": failed,
        "in_progress": in_progress,
        "remaining": remaining,
        "elapsed_minutes": round(elapsed / 60, 1),
        "eta_minutes": round(eta_seconds / 60, 1),
        "avg_per_song_minutes": round(avg_per_song / 60, 1),
        "updated_at": time.strftime('%Y-%m-%d %H:%M:%S'),
        "completed_songs": [],
        "failed_songs": []
    }
    
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write
    tmp = PROGRESS_FILE.with_suffix(".tmp")
    with open(tmp, 'w') as f:
        json.dump(progress, f, indent=2)
    tmp.rename(PROGRESS_FILE)


# ─────────────────────────────────────────────────
# 4. PROCESS SINGLE SONG (with lock + error log)
# ─────────────────────────────────────────────────

def process_single_song(mp3_path_str, txt_path_str):
    """
    Process a single song with all safeguards:
    - Lock file to prevent duplicate processing
    - Per-song error log
    - Atomic lyrics.json write
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
    
    # Lock file — prevent duplicate processing
    if not _acquire_lock(song_name):
        print(f"  🔒 {song_name} is already being processed. Skipping.")
        return {"song": song_name, "status": "skipped", "duration": 0}
    
    try:
        # Read lyrics from the EXACT matched txt file (no ambiguity)
        raw_text = open(txt_path, "r", encoding="utf-8").read()
        ground_truth_text = extract_lyrics_from_text(raw_text)
        
        if not ground_truth_text or not ground_truth_text.strip():
            raise ValueError(f"Empty lyrics extracted from {txt_path.name}")
        
        # Execute pipeline
        pipeline.main(
            str(mp3_path),
            ground_truth_text=ground_truth_text
        )
        
        # Clear any previous error log on success
        error_log = song_dir / "error.log"
        if error_log.exists():
            error_log.unlink()
        
        duration = time.time() - start_time
        return {"song": song_name, "status": "success", "duration": round(duration, 1)}
        
    except Exception as e:
        duration = time.time() - start_time
        error_msg = f"{type(e).__name__}: {e}"
        
        # Save detailed error log
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
        
        print(f"\n>>> ERROR: {song_name}: {error_msg}")
        print(f"    Details saved to: {error_log}")
        return {"song": song_name, "status": "failed", "error": error_msg, "duration": round(duration, 1)}
    
    finally:
        _release_lock(song_name)


# ─────────────────────────────────────────────────
# 5. BATCH ORCHESTRATOR
# ─────────────────────────────────────────────────

def _is_completed(song_name):
    """Check if a song has already been fully processed (has .mp4 output)."""
    song_dir = OUTPUT_FOLDER / song_name
    if not song_dir.exists():
        return False
    return len(list(song_dir.glob("*.mp4"))) > 0


def process_batch(max_workers=1, retry_failed=True):
    """
    Batch process all songs with full safeguards.
    
    Features:
    - Pre-flight validation (no wrong file matches)
    - Lock files (no duplicate processing)
    - Atomic writes (no corrupted files)
    - Per-song error logs
    - Live progress dashboard
    """
    print(f"\n{'='*60}")
    print(f"       BATCH PROCESSOR (NeMo Alignment, Workers: {max_workers})")
    print(f"{'='*60}")

    INPUT_FOLDER.mkdir(exist_ok=True)
    GROUND_TRUTH_FOLDER.mkdir(exist_ok=True)
    OUTPUT_FOLDER.mkdir(exist_ok=True)

    # Step 1: Pre-flight validation
    pairs, no_lyrics = validate_pairs()
    
    if not pairs:
        print("No valid MP3↔TXT pairs found. Nothing to process.")
        return

    # Filter out already completed songs
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
    
    # Time estimate
    est_per_song = 4.5  # minutes (based on real measurement)
    est_parallel = remaining * est_per_song / max_workers
    print(f"\nEstimated time: ~{est_parallel:.0f} min ({est_parallel/60:.1f} hours) with {max_workers} workers")
    print(f"Started at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"\n{'='*60}\n")
    
    # Step 2: Process
    results = {"success": 0, "failed": 0, "skipped": 0}
    completed_songs = []
    failed_songs = []
    batch_start = time.time()
    
    if max_workers == 1:
        # Sequential mode
        for i, (mp3_path, txt_path) in enumerate(to_process, 1):
            print(f"\n>>> [{i}/{remaining}] Processing: {mp3_path.name}")
            print(f"    Lyrics: {txt_path.name}")
            
            result = process_single_song(str(mp3_path), str(txt_path))
            
            if result["status"] == "success":
                results["success"] += 1
                completed_songs.append(result["song"])
            elif result["status"] == "skipped":
                results["skipped"] += 1
            else:
                results["failed"] += 1
                failed_songs.append({"song": result["song"], "error": result.get("error", "unknown")})
            
            # Update progress dashboard
            _update_progress(remaining, results["success"], results["failed"], 1, batch_start)
            
            # Progress report
            elapsed = time.time() - batch_start
            songs_done = results["success"] + results["failed"] + results["skipped"]
            avg_time = elapsed / max(songs_done, 1)
            eta = avg_time * (remaining - songs_done)
            print(f"\n>>> Progress: {songs_done}/{remaining} | ✅ {results['success']} ❌ {results['failed']} | ETA: {eta/60:.0f} min")
    else:
        # Parallel mode — each worker gets a unique (mp3, txt) pair
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for mp3_path, txt_path in to_process:
                future = executor.submit(process_single_song, str(mp3_path), str(txt_path))
                futures[future] = mp3_path.name
            
            for i, future in enumerate(as_completed(futures), 1):
                song_name = futures[future]
                try:
                    result = future.result(timeout=1800)  # 30 min timeout
                    
                    if result["status"] == "success":
                        results["success"] += 1
                        completed_songs.append(result["song"])
                        print(f"\n>>> [{i}/{remaining}] ✅ {song_name} ({result['duration']:.0f}s)")
                    elif result["status"] == "skipped":
                        results["skipped"] += 1
                        print(f"\n>>> [{i}/{remaining}] 🔒 {song_name} (skipped - already processing)")
                    else:
                        results["failed"] += 1
                        failed_songs.append({"song": result["song"], "error": result.get("error", "unknown")})
                        print(f"\n>>> [{i}/{remaining}] ❌ {song_name}: {result.get('error', 'unknown')}")
                        
                except Exception as e:
                    results["failed"] += 1
                    failed_songs.append({"song": song_name, "error": str(e)})
                    print(f"\n>>> [{i}/{remaining}] ❌ {song_name}: {e}")
                
                # Update dashboard
                in_prog = remaining - results["success"] - results["failed"] - results["skipped"]
                _update_progress(remaining, results["success"], results["failed"], min(in_prog, max_workers), batch_start)
                
                # ETA
                elapsed = time.time() - batch_start
                songs_done = results["success"] + results["failed"] + results["skipped"]
                if songs_done > 0:
                    avg_time = elapsed / songs_done
                    eta = avg_time * (remaining - songs_done) / max_workers
                    print(f"    Progress: {songs_done}/{remaining} | ✅ {results['success']} ❌ {results['failed']} | ETA: {eta/60:.0f} min")
    
    # Final report
    total_time = time.time() - batch_start
    print(f"\n{'='*60}")
    print(f"       BATCH COMPLETE")
    print(f"{'='*60}")
    print(f"  Total processed: {results['success'] + results['failed']}")
    print(f"  ✅ Success: {results['success']}")
    print(f"  ❌ Failed: {results['failed']}")
    if results['skipped'] > 0:
        print(f"  🔒 Skipped (locked): {results['skipped']}")
    print(f"  ⏱️  Total time: {total_time/60:.1f} min ({total_time/3600:.1f} hours)")
    if results['success'] > 0:
        print(f"  📊 Avg per song: {total_time/max(results['success'],1)/60:.1f} min")
    print(f"{'='*60}")
    
    if failed_songs:
        print(f"\nFailed songs:")
        for f in failed_songs:
            print(f"  - {f['song']}: {f.get('error', 'unknown')}")
            error_log = OUTPUT_FOLDER / f['song'] / "error.log"
            if error_log.exists():
                print(f"    → See: {error_log}")
        print(f"\nRe-run with: ./start  (failed songs will be retried)")
    
    # Final progress update
    progress_data = {
        "total": remaining,
        "completed": results["success"],
        "failed": results["failed"],
        "in_progress": 0,
        "remaining": 0,
        "elapsed_minutes": round(total_time / 60, 1),
        "eta_minutes": 0,
        "avg_per_song_minutes": round(total_time / max(results['success'], 1) / 60, 1),
        "updated_at": time.strftime('%Y-%m-%d %H:%M:%S'),
        "status": "COMPLETE",
        "completed_songs": completed_songs,
        "failed_songs": failed_songs
    }
    tmp = PROGRESS_FILE.with_suffix(".tmp")
    with open(tmp, 'w') as f:
        json.dump(progress_data, f, indent=2, ensure_ascii=False)
    tmp.rename(PROGRESS_FILE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch Process Lyric Videos (NeMo Alignment)")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers (default: 1)")
    parser.add_argument("--no-retry", action="store_true", help="Don't retry previously failed songs")
    args = parser.parse_args()
    
    process_batch(
        max_workers=args.workers,
        retry_failed=not args.no_retry
    )
