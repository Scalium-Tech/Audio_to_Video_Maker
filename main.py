import os
import json
import shutil
import subprocess
import argparse
from dotenv import load_dotenv
load_dotenv()

from nemo_align import align_with_nemo
from lyrics_extractor import extract_lyrics_from_text, add_punctuation_with_gemini
from generate_background import generate_background_image, get_lyrics_text_from_json

from pathlib import Path

# Path to the Remotion video project
VIDEO_PROJECT_DIR = Path(__file__).parent / "video"


def render_video(audio_path, lyrics_path, output_video_path):
    """
    Copies assets to Remotion's public folder, renders the video,
    and saves the final MP4 to the song's output folder.
    """
    print(f"\n--- Step 4: Rendering lyric video with Remotion ---")

    public_dir = VIDEO_PROJECT_DIR / "public"
    public_dir.mkdir(parents=True, exist_ok=True)

    # Copy audio and lyrics to Remotion's public folder
    shutil.copy2(str(audio_path), str(public_dir / "audio.mp3"))
    shutil.copy2(str(lyrics_path), str(public_dir / "lyrics.json"))
    print(f"Assets copied to {public_dir}")

    # Generate background image based on song content
    song_name = Path(audio_path).stem
    lyrics_text = get_lyrics_text_from_json(str(lyrics_path))
    bg_image_path = str(public_dir / "background.jpg")
    generate_background_image(song_name, lyrics_text, bg_image_path)

    # Ensure output directory exists and use absolute path
    output_video_path = Path(output_video_path).resolve()
    output_video_path.parent.mkdir(parents=True, exist_ok=True)

    # Render the video using Remotion CLI
    render_cmd = f'npx remotion render LyricVideo "{output_video_path}" --concurrency=100% --log=error'

    print(f"Rendering video... (this may take a few minutes)")
    try:
        result = subprocess.run(
            render_cmd,
            cwd=str(VIDEO_PROJECT_DIR),
            shell=True,
        )
        if result.returncode == 0:
            print(f"--- SUCCESS: Video saved to {output_video_path} ---")
            return True
        else:
            print(f"Remotion render failed with exit code {result.returncode}")
            return False
    except Exception as e:
        print(f"Error running Remotion render: {e}")
        return False


def main(audio_path, ground_truth_text=None, **kwargs):
    """
    Main pipeline:
      1. Extract + punctuate lyrics (Gemini)
      2. NeMo forced alignment (precise timestamps)
      3. Render video (Remotion)
    """
    audio_file = Path(audio_path)
    if not audio_file.exists():
        print(f"Error: Audio file not found at {audio_path}")
        return

    song_name = audio_file.stem

    # Create output directory
    output_base_dir = Path("output_song")
    song_output_dir = output_base_dir / song_name
    song_output_dir.mkdir(parents=True, exist_ok=True)

    print(f"--- Output will be saved to: {song_output_dir} ---")

    lyrics_file = song_output_dir / "lyrics.json"

    # Step 1: Prepare lyrics (extract + punctuate)
    if not lyrics_file.exists():
        print(f"\n--- Step 1: Preparing Lyrics ---")
        
        if ground_truth_text:
            lyrics_text = ground_truth_text
            print(f"  Using provided ground truth lyrics ({len(lyrics_text.splitlines())} lines)")
        else:
            # Try to find matching txt file
            txt_path = _find_ground_truth_file(audio_file)
            if txt_path:
                raw_text = open(txt_path, "r", encoding="utf-8").read()
                lyrics_text = extract_lyrics_from_text(raw_text)
                print(f"  Extracted lyrics from: {txt_path.name}")
            else:
                print(f"  ERROR: No lyrics found for {song_name}")
                print(f"  Please add a .txt file in ground_truth_lyrics/")
                return

        # Add punctuation via Gemini
        print(f"\n--- Step 2: Adding Punctuation (Gemini) ---")
        try:
            punctuated = add_punctuation_with_gemini(lyrics_text)
            if punctuated:
                lyrics_text = punctuated
                print(f"  Punctuation added successfully")
        except Exception as e:
            print(f"  Punctuation failed: {e}. Continuing without.")

        # Step 2: NeMo forced alignment
        print(f"\n--- Step 3: NeMo Forced Alignment ---")
        result = align_with_nemo(
            str(audio_file),
            lyrics_text,
            str(lyrics_file)
        )

        if not result:
            print("  NeMo alignment failed.")
            # Fallback to Gemini alignment
            print("  Falling back to Gemini alignment...")
            try:
                from gemini_align import full_pipeline_gemini
                result = full_pipeline_gemini(str(audio_path), lyrics_text)
                if result:
                    with open(lyrics_file, 'w', encoding='utf-8') as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    print(f"  Gemini fallback: {len(result)} segments saved")
                else:
                    print("  Both NeMo and Gemini failed. Aborting.")
                    return
            except Exception as e:
                print(f"  Gemini fallback also failed: {e}")
                return
    else:
        print(f"\nLyrics already exist at: {lyrics_file}. Skipping alignment.")

    # Step 3: Render Video
    print(f"\n--- Step 4: Rendering Final Video ---")
    video_output = song_output_dir / f"{song_name}.mp4"
    render_video(audio_file, lyrics_file, video_output)

    print(f"\n{'='*60}")
    print(f"  ALL DONE! Your files are in: {song_output_dir}")
    print(f"  - lyrics.json  (timestamped lyrics)")
    print(f"  - {song_name}.mp4  (lyric video)")
    print(f"{'='*60}")


def _find_ground_truth_file(audio_path):
    """Find matching ground truth lyrics file."""
    audio_path = Path(audio_path)
    gt_dir = Path("ground_truth_lyrics")
    
    if not gt_dir.exists():
        return None
    
    # Try: ground_truth_lyrics/<audio_name>.mp3.txt
    candidates = [
        gt_dir / f"{audio_path.name}.txt",
        gt_dir / f"{audio_path.stem}.txt",
    ]
    
    for c in candidates:
        if c.exists():
            return c
    
    # Fuzzy match
    for f in gt_dir.glob("*.txt"):
        if audio_path.stem[:20] in f.name:
            return f
    
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LyricFlow — Audio to Lyric Video Pipeline")
    parser.add_argument("audio", help="Path to MP3 file")
    parser.add_argument("--lyrics", default=None, help="Path to lyrics .txt file (optional)")

    args = parser.parse_args()
    
    ground_truth_text = None
    if args.lyrics:
        raw = open(args.lyrics, "r", encoding="utf-8").read()
        ground_truth_text = extract_lyrics_from_text(raw)
    
    main(args.audio, ground_truth_text=ground_truth_text)
