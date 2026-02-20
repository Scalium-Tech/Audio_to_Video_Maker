import os
import time
from pathlib import Path
import argparse
import main as pipeline

# Configuration
INPUT_FOLDER = Path("input_songs")
GROUND_TRUTH_FOLDER = Path("ground_truth_lyrics")

def process_batch(model_name="large-v2"):
    """
    Scans the input_songs folder for MP3 files and processes them one by one.
    Automatically uses ground_truth_lyrics if available for Post-Alignment Injection.
    """
    print(f"\n{'='*60}")
    print(f"       BATCH PROCESSOR: STARTING BATCH RUN (Model: {model_name})")
    print(f"{'='*60}")

    if not INPUT_FOLDER.exists():
        os.makedirs(INPUT_FOLDER)
        print(f"Created {INPUT_FOLDER} folder. Please add MP3 files there.")
        return

    if not GROUND_TRUTH_FOLDER.exists():
        os.makedirs(GROUND_TRUTH_FOLDER)
        print(f"Created {GROUND_TRUTH_FOLDER} folder for ground truth lyrics.")

    # Find all MP3 files
    song_files = list(INPUT_FOLDER.glob("*.mp3"))
    
    if not song_files:
        print(f"No MP3 files found in {INPUT_FOLDER}. Please upload some songs!")
        return

    print(f"Found {len(song_files)} songs to process.\n")

    for i, song_path in enumerate(song_files, 1):
        print(f"\n>>> PROCESSING SONG {i}/{len(song_files)}: {song_path.name}")
        start_time = time.time()
        
        try:
            processing_language = "auto"
            lyrics_text = None
            ground_truth_text = None
            
            # Check for GROUND TRUTH LYRICS file
            txt_path = GROUND_TRUTH_FOLDER / (song_path.stem + ".txt")
            if txt_path.exists():
                print(f"--- FOUND GROUND TRUTH LYRICS: {txt_path.name} ---")
                try:
                    with open(txt_path, "r", encoding="utf-8") as f:
                        ground_truth_text = f.read()
                    print(f"--- Triggering Post-Alignment Injection Protocol ---")
                except Exception as e:
                    print(f"Warning: Could not read ground truth file: {e}")

            # Execute pipeline
            pipeline.main(
                str(song_path), 
                language=processing_language, 
                model_name=model_name, 
                lyrics_text=lyrics_text,
                ground_truth_text=ground_truth_text
            )
            
            duration = time.time() - start_time
            print(f"\n>>> SUCCESS: Finished {song_path.name} in {duration:.2f}s")
        except Exception as e:
            print(f"\n>>> ERROR: Failed to process {song_path.name}")
            print(f"Details: {e}")
            continue

    print(f"\n{'='*60}")
    print(f"       BATCH PROCESSOR: ALL JOBS COMPLETE")
    print(f"{'='*60}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch Process Lyric Videos")
    parser.add_argument("--model", default="large-v2", help="Whisper model to use (default: large-v2)")
    args = parser.parse_args()
    
    process_batch(model_name=args.model)
