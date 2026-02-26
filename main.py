import os
import json
import shutil
import subprocess
import argparse
from dotenv import load_dotenv
load_dotenv()  # Automatically reads .env file
from audio_utils import isolate_vocals
from transcribe_engine import transcribe_and_align
from text_refinery import refine_lyrics_with_gemini, inject_lyrics_with_gemini
from gemini_align import align_and_split_lyrics, full_pipeline_gemini
from generate_background import generate_background_image, get_lyrics_text_from_json

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

    # Generate background image based on song content
    song_name = Path(audio_path).stem
    lyrics_text = get_lyrics_text_from_json(str(lyrics_path))
    bg_image_path = str(public_dir / "background.jpg")
    generate_background_image(song_name, lyrics_text, bg_image_path)

    # Ensure output directory exists and use absolute path
    output_video_path = Path(output_video_path).resolve()
    output_video_path.parent.mkdir(parents=True, exist_ok=True)

    # Render the video using Remotion CLI
    # shell=True is required on Windows for npx to work in subprocess
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


def main(audio_path, language="hi", api_key=None, model_name="large-v2", lyrics_text=None, vocal_override_path=None, ground_truth_text=None, skip_isolation=False):
    # Extract song name for folder creation
    audio_file = Path(audio_path)
    if not audio_file.exists():
        print(f"Error: Playback file not found at {audio_path}")
        return

    song_name = audio_file.stem  # e.g., "Kal Se Pakka"

    # Create output directory structure: output_song / song_name
    output_base_dir = Path("output_song")
    song_output_dir = output_base_dir / song_name
    song_output_dir.mkdir(parents=True, exist_ok=True)

    print(f"--- Output will be saved to: {song_output_dir} ---")

    # 1. Isolate Vocals (or skip if --skip-isolation)
    print(f"\n--- Step 1: Vocal Isolation ---")
    
    if vocal_override_path:
        print(f"*** USING MANUAL VOCAL OVERRIDE: {vocal_override_path} ***")
        vocal_audio = vocal_override_path
        if not Path(vocal_audio).exists():
             print(f"Error: Override file not found: {vocal_audio}")
             return
    elif skip_isolation:
        print(f"⚡ SKIPPING vocal isolation (using original MP3 directly)")
        vocal_audio = str(audio_path)
    else:
        # Optimization: Skip if we already have the clean vocal file
        vocal_final_path = Path("separated") / f"{song_name}_vocal_clean.wav"
        if vocal_final_path.exists():
            print(f"Clean vocals already exist at: {vocal_final_path}. Skipping isolation step.")
            vocal_audio = str(vocal_final_path)
        else:
            vocal_audio = isolate_vocals(audio_path)
            
        if not vocal_audio:
            print("Failed to isolate vocals.")
            return

    # 2. Process lyrics
    lyrics_file = song_output_dir / "lyrics.json"
    
    if lyrics_file.exists() and not lyrics_text:
        print(f"Lyrics already exist at: {lyrics_file}. Skipping.")
    elif ground_truth_text and skip_isolation:
        # ⚡ FAST PATH: Skip WhisperX entirely, do everything with Gemini
        print(f"\n--- ⚡ FAST PATH: Direct Gemini Pipeline (skipping WhisperX) ---")
        try:
            fast_result = full_pipeline_gemini(str(audio_path), ground_truth_text, api_key=api_key)
            if fast_result:
                with open(lyrics_file, 'w', encoding='utf-8') as f:
                    json.dump(fast_result, f, ensure_ascii=False, indent=2)
                print(f"--- Lyrics saved to {lyrics_file} ---")
            else:
                print("  Fast path failed. Falling back to WhisperX...")
                # Fall through to old path
                ground_truth_text_fallback = ground_truth_text
                ground_truth_text = None  # Reset so we don't loop
                # Run old path below
        except Exception as e:
            print(f"  Fast path error: {e}. Falling back to WhisperX...")
            fast_result = None
        
        if not lyrics_file.exists():
            # Fallback: run old WhisperX path
            ground_truth_text = ground_truth_text_fallback if 'ground_truth_text_fallback' in dir() else ground_truth_text
    
    if not lyrics_file.exists():
        # Standard path: WhisperX → Injection → Alignment
        print(f"\n--- Step 2: Transcription & Alignment ---")
        raw_segments = transcribe_and_align(vocal_audio, language=language, device="cpu", model_name=model_name, lyrics_text=lyrics_text)
        if not raw_segments:
            print("Transcription failed. No segments produced.")
            return

        timing_shell_file = song_output_dir / "timing_shell.json"
        with open(timing_shell_file, 'w', encoding='utf-8') as f:
            json.dump(raw_segments, f, ensure_ascii=False, indent=2)
        print(f"Timing Shell saved to {timing_shell_file}")

        print(f"\n--- Step 3: Gemini Processing ---")
        try:
            if ground_truth_text:
                refined_lyrics = inject_lyrics_with_gemini(raw_segments, ground_truth_text, language=language, api_key=api_key)
            else:
                refined_lyrics = refine_lyrics_with_gemini(raw_segments, language=language, api_key=api_key)
        except Exception as e:
            print(f"Gemini processing crashed: {e}")
            refined_lyrics = None

        if refined_lyrics:
            with open(lyrics_file, 'w', encoding='utf-8') as f:
                json.dump(refined_lyrics, f, ensure_ascii=False, indent=2)
            print(f"--- Step 4: Lyrics saved to {lyrics_file} ---")

            print(f"\n--- Step 3.5: Gemini Align + Split (merged) ---")
            try:
                refined_lyrics = align_and_split_lyrics(vocal_audio, refined_lyrics, api_key=api_key)
                with open(lyrics_file, 'w', encoding='utf-8') as f:
                    json.dump(refined_lyrics, f, ensure_ascii=False, indent=2)
                print(f"  Aligned lyrics saved to {lyrics_file}")
            except Exception as e:
                print(f"  Gemini alignment failed: {e}. Keeping original timestamps.")
        else:
            print("LLM processing failed. Saving raw transcription as fallback...")
            fallback_data = [{"text": s["text"], "start": s["start"], "end": s["end"]} for s in raw_segments]
            with open(lyrics_file, 'w', encoding='utf-8') as f:
                json.dump(fallback_data, f, ensure_ascii=False, indent=2)
            print(f"--- Step 4 (FALLBACK): Raw lyrics saved to {lyrics_file} ---")

    # 5. Render Video with Remotion
    print(f"\n--- Step 5: Rendering Final Video ---")
    video_output = song_output_dir / f"{song_name}.mp4"
    render_video(audio_file, lyrics_file, video_output)

    print(f"\n{'='*60}")
    print(f"  ALL DONE! Your files are in: {song_output_dir}")
    print(f"  - lyrics.json  (timestamped lyrics)")
    print(f"  - {song_name}.mp4  (lyric video)")
    print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lyric Extraction & Video Pipeline")
    parser.add_argument("audio", help="Path to MP3 file")
    parser.add_argument("--language", default="hi", help="Language code: hi=Hindi, mr=Marathi (default: hi)")
    parser.add_argument("--api-key", default=None, help="Google Gemini API key (or set GEMINI_API_KEY env var)")
    parser.add_argument("--model", default="large-v2", help="Whisper model to use (default: large-v2)")

    args = parser.parse_args()
    main(args.audio, language=args.language, api_key=args.api_key, model_name=args.model)
