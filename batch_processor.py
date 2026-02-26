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
PROGRESS_FILE = OUTPUT_FOLDER / "batch_progress.json"


def _load_progress():
    """Load progress from previous runs."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    return {"completed": [], "failed": [], "skipped": []}


def _save_progress(progress):
    """Save progress to disk."""
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, indent=2)


def _is_completed(song_name):
    """Check if a song has already been fully processed (has .mp4 output)."""
    song_dir = OUTPUT_FOLDER / song_name
    if not song_dir.exists():
        return False
    mp4_files = list(song_dir.glob("*.mp4"))
    return len(mp4_files) > 0


def _find_ground_truth(song_path):
    """Find matching ground truth lyrics file for a song."""
    txt_candidates = [
        GROUND_TRUTH_FOLDER / (song_path.stem + ".txt"),
        GROUND_TRUTH_FOLDER / (song_path.name + ".txt"),
    ]
    for txt_file in GROUND_TRUTH_FOLDER.glob("*.txt"):
        if song_path.stem in txt_file.name and txt_file not in txt_candidates:
            txt_candidates.append(txt_file)
    
    for candidate in txt_candidates:
        if candidate.exists():
            return candidate
    return None


def process_single_song(song_path_str, model_name="large-v2"):
    """Process a single song. Designed to run in a separate process."""
    # Import inside function to avoid multiprocessing issues
    from dotenv import load_dotenv
    load_dotenv()
    
    import main as pipeline
    from lyrics_extractor import extract_lyrics_from_text
    
    song_path = Path(song_path_str)
    song_name = song_path.stem
    start_time = time.time()
    
    try:
        ground_truth_text = None
        txt_path = _find_ground_truth(song_path)
        
        if txt_path:
            try:
                raw_text = open(txt_path, "r", encoding="utf-8").read()
                ground_truth_text = extract_lyrics_from_text(raw_text)
            except Exception as e:
                print(f"  Warning: Could not read ground truth for {song_name}: {e}")
        
        # Execute pipeline
        pipeline.main(
            str(song_path),
            language="auto",
            model_name=model_name,
            lyrics_text=None,
            ground_truth_text=ground_truth_text
        )
        
        duration = time.time() - start_time
        return {"song": song_name, "status": "success", "duration": round(duration, 1)}
        
    except Exception as e:
        duration = time.time() - start_time
        error_msg = f"{type(e).__name__}: {e}"
        print(f"\n>>> ERROR: Failed to process {song_name}: {error_msg}")
        traceback.print_exc()
        return {"song": song_name, "status": "failed", "error": error_msg, "duration": round(duration, 1)}


def process_batch(model_name="large-v2", max_workers=3, retry_failed=True):
    """
    Batch process all songs in input_songs/ with parallel workers.
    
    Features:
    - Skips already completed songs (has .mp4)
    - Parallel processing with configurable workers
    - Progress tracking with resume capability
    - Retry failed songs
    """
    print(f"\n{'='*60}")
    print(f"       BATCH PROCESSOR (Model: {model_name}, Workers: {max_workers})")
    print(f"{'='*60}")

    INPUT_FOLDER.mkdir(exist_ok=True)
    GROUND_TRUTH_FOLDER.mkdir(exist_ok=True)
    OUTPUT_FOLDER.mkdir(exist_ok=True)

    # Find all MP3 files
    song_files = sorted(INPUT_FOLDER.glob("*.mp3"))
    
    if not song_files:
        print(f"No MP3 files found in {INPUT_FOLDER}.")
        return

    # Load progress and determine what to process
    progress = _load_progress()
    
    # Filter out already completed songs
    to_process = []
    skipped = 0
    for song_path in song_files:
        song_name = song_path.stem
        if _is_completed(song_name):
            skipped += 1
            if song_name not in progress["completed"]:
                progress["completed"].append(song_name)
        elif retry_failed or song_name not in [f.get("song") if isinstance(f, dict) else f for f in progress.get("failed", [])]:
            to_process.append(song_path)
    
    total = len(song_files)
    already_done = skipped
    remaining = len(to_process)
    
    print(f"\nTotal songs: {total}")
    print(f"Already completed: {already_done} (skipped)")
    print(f"To process: {remaining}")
    
    if remaining == 0:
        print("\nAll songs already processed! Nothing to do.")
        _save_progress(progress)
        return
    
    # Estimate time
    est_per_song = 7  # minutes
    est_sequential = remaining * est_per_song
    est_parallel = est_sequential / max_workers
    print(f"\nEstimated time: ~{est_parallel:.0f} min ({est_parallel/60:.1f} hours) with {max_workers} workers")
    print(f"Started at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"\n{'='*60}\n")
    
    # Process songs in parallel
    results = {"success": 0, "failed": 0}
    batch_start = time.time()
    
    if max_workers == 1:
        # Sequential mode — simpler, better for debugging
        for i, song_path in enumerate(to_process, 1):
            print(f"\n>>> [{i}/{remaining}] Processing: {song_path.name}")
            result = process_single_song(str(song_path), model_name)
            
            if result["status"] == "success":
                results["success"] += 1
                progress["completed"].append(result["song"])
            else:
                results["failed"] += 1
                progress["failed"].append({"song": result["song"], "error": result.get("error", "unknown")})
            
            _save_progress(progress)
            
            # Progress report
            elapsed = time.time() - batch_start
            songs_done = results["success"] + results["failed"]
            avg_time = elapsed / songs_done if songs_done > 0 else est_per_song * 60
            eta = avg_time * (remaining - songs_done)
            print(f"\n>>> Progress: {songs_done}/{remaining} | ✅ {results['success']} ❌ {results['failed']} | ETA: {eta/60:.0f} min")
    else:
        # Parallel mode
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for song_path in to_process:
                future = executor.submit(process_single_song, str(song_path), model_name)
                futures[future] = song_path.name
            
            for i, future in enumerate(as_completed(futures), 1):
                song_name = futures[future]
                try:
                    result = future.result(timeout=1800)  # 30 min timeout per song
                    
                    if result["status"] == "success":
                        results["success"] += 1
                        progress["completed"].append(result["song"])
                        print(f"\n>>> [{i}/{remaining}] ✅ {song_name} ({result['duration']:.0f}s)")
                    else:
                        results["failed"] += 1
                        progress["failed"].append({"song": result["song"], "error": result.get("error", "unknown")})
                        print(f"\n>>> [{i}/{remaining}] ❌ {song_name}: {result.get('error', 'unknown')}")
                        
                except Exception as e:
                    results["failed"] += 1
                    progress["failed"].append({"song": song_name, "error": str(e)})
                    print(f"\n>>> [{i}/{remaining}] ❌ {song_name}: {e}")
                
                _save_progress(progress)
                
                # ETA
                elapsed = time.time() - batch_start
                songs_done = results["success"] + results["failed"]
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
    print(f"  ⏱️  Total time: {total_time/60:.1f} min ({total_time/3600:.1f} hours)")
    if results['success'] > 0:
        print(f"  📊 Avg per song: {total_time/max(results['success'],1)/60:.1f} min")
    print(f"{'='*60}")
    
    if results['failed'] > 0:
        print(f"\nFailed songs:")
        for f in progress["failed"][-results['failed']:]:
            if isinstance(f, dict):
                print(f"  - {f['song']}: {f.get('error', 'unknown')}")
        print(f"\nRe-run with: ./start  (failed songs will be retried)")
    
    _save_progress(progress)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch Process Lyric Videos")
    parser.add_argument("--model", default="large-v2", help="Whisper model (default: large-v2)")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers (default: 1, recommended: 3)")
    parser.add_argument("--no-retry", action="store_true", help="Don't retry previously failed songs")
    args = parser.parse_args()
    
    process_batch(
        model_name=args.model,
        max_workers=args.workers,
        retry_failed=not args.no_retry
    )
