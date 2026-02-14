import os
import json
import shutil
import subprocess
import argparse
from dotenv import load_dotenv
load_dotenv()  # Automatically reads .env file
from audio_utils import isolate_vocals
from transcribe_engine import transcribe_and_align
from text_refinery import refine_lyrics_with_gemini

from pathlib import Path

# Path to the Remotion video project
VIDEO_PROJECT_DIR = Path(__file__).parent / "video"


def render_video(audio_path, lyrics_path, output_video_path):
    """
    Copies assets to Remotion's public folder, renders the video,
    and saves the final MP4 to the song's output folder.
    """
    print(f"\n--- Step 5: Rendering lyric video with Remotion ---")

    public_dir = VIDEO_PROJECT_DIR / "public"
    public_dir.mkdir(parents=True, exist_ok=True)

    # Copy audio and lyrics to Remotion's public folder
    shutil.copy2(str(audio_path), str(public_dir / "audio.mp3"))
    shutil.copy2(str(lyrics_path), str(public_dir / "lyrics.json"))
    print(f"Assets copied to {public_dir}")

    # Ensure output directory exists and use absolute path
    output_video_path = Path(output_video_path).resolve()
    output_video_path.parent.mkdir(parents=True, exist_ok=True)

    # Render the video using Remotion CLI
    # shell=True is required on Windows for npx to work in subprocess
    render_cmd = f'npx remotion render LyricVideo "{output_video_path}"'

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


def main(audio_path, api_key=None):
    # Extract song name for folder creation
    audio_file = Path(audio_path)
    song_name = audio_file.stem  # e.g., "Kal Se Pakka"

    # Create output directory structure: output_song / song_name
    output_base_dir = Path("output_song")
    song_output_dir = output_base_dir / song_name
    song_output_dir.mkdir(parents=True, exist_ok=True)

    print(f"--- Output will be saved to: {song_output_dir} ---")

    # 1. Isolate Vocals
    print(f"\n--- Step 1: Vocal Isolation ---")
    vocal_audio = isolate_vocals(audio_path)
    if not vocal_audio:
        print("Failed to isolate vocals.")
        return

    # 2. Transcribe and Align
    print(f"\n--- Step 2: Transcription & Alignment ---")
    raw_segments = transcribe_and_align(vocal_audio, device="cpu")

    # 3. Refine with Gemini
    print(f"\n--- Step 3: Gemini Refinement ---")
    refined_lyrics = refine_lyrics_with_gemini(raw_segments, api_key=api_key)

    # 4. Save lyrics.json
    lyrics_file = song_output_dir / "lyrics.json"
    if refined_lyrics:
        with open(lyrics_file, 'w', encoding='utf-8') as f:
            json.dump(refined_lyrics, f, ensure_ascii=False, indent=2)
        print(f"--- Step 4: Lyrics saved to {lyrics_file} ---")
    else:
        # Fallback: save raw transcription
        print("LLM refinement failed. Saving raw transcription as fallback...")
        fallback_data = [{"text": s["text"], "start": s["start"], "end": s["end"]} for s in raw_segments]
        with open(lyrics_file, 'w', encoding='utf-8') as f:
            json.dump(fallback_data, f, ensure_ascii=False, indent=2)
        print(f"--- Step 4 (FALLBACK): Raw lyrics saved to {lyrics_file} ---")

    # 5. Render Video with Remotion
    video_output = song_output_dir / f"{song_name}.mp4"
    render_video(audio_file, lyrics_file, video_output)

    print(f"\n{'='*60}")
    print(f"  ALL DONE! Your files are in: {song_output_dir}")
    print(f"  - lyrics.json  (timestamped lyrics)")
    print(f"  - {song_name}.mp4  (lyric video)")
    print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hindi Lyric Extraction & Video Pipeline")
    parser.add_argument("audio", help="Path to Hindi MP3 file")
    parser.add_argument("--api-key", default=None, help="Google Gemini API key (or set GEMINI_API_KEY env var)")

    args = parser.parse_args()
    main(args.audio, args.api_key)
