import os
import time
from pathlib import Path
import argparse
import main as pipeline
from lyrics_extractor import extract_lyrics_from_text

# Configuration
INPUT_FOLDER = Path("input_songs")
GROUND_TRUTH_FOLDER = Path("ground_truth_lyrics")

def process_batch(model_name="large-v2"):
    """
    Scans the input_songs folder for MP3 files and processes them one by one.
    Automatically uses ground_truth_lyrics if available for Post-Alignment Injection.
    Lyrics are auto-extracted from Suno AI export files or any messy txt files.
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
            # Support both patterns: "songname.txt" and "songname.mp3.txt"
            txt_candidates = [
                GROUND_TRUTH_FOLDER / (song_path.stem + ".txt"),       # songname.txt
                GROUND_TRUTH_FOLDER / (song_path.name + ".txt"),       # songname.mp3.txt
            ]
            # Also check for any txt file that contains the song name
            for txt_file in GROUND_TRUTH_FOLDER.glob("*.txt"):
                if song_path.stem in txt_file.name and txt_file not in txt_candidates:
                    txt_candidates.append(txt_file)
            
            txt_path = None
            for candidate in txt_candidates:
                if candidate.exists():
                    txt_path = candidate
                    break
            
            if txt_path:
                print(f"--- FOUND GROUND TRUTH LYRICS: {txt_path.name} ---")
                try:
                    with open(txt_path, "r", encoding="utf-8") as f:
                        raw_text = f.read()
                    # Auto-extract clean Devanagari lyrics from the raw file
                    ground_truth_text = extract_lyrics_from_text(raw_text)
                    lyric_lines = len(ground_truth_text.splitlines()) if ground_truth_text else 0
                    print(f"--- Extracted {lyric_lines} clean lyric lines from txt file ---")
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
