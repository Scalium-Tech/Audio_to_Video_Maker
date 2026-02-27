import os
import json
import shutil
import subprocess
import argparse
import time
from dotenv import load_dotenv
load_dotenv()

from nemo_align import align_with_nemo
from lyrics_extractor import extract_lyrics_from_text, add_punctuation_with_gemini
from generate_background import generate_background_image, get_lyrics_text_from_json
from ffmpeg_render import render_with_ffmpeg as ffmpeg_render_video

from pathlib import Path

# Path to the Remotion video project
VIDEO_PROJECT_DIR = Path(__file__).parent / "video"


def render_video_remotion(audio_path, lyrics_path, output_video_path):
    """
    Renders the lyric video using Remotion.
    
    Parallel-safe: Each call uses unique filenames in video/public/
    and passes them via --props so multiple workers can render simultaneously.
    """
    print(f"\n--- Step 4: Rendering lyric video with Remotion ---")

    public_dir = VIDEO_PROJECT_DIR / "public"
    public_dir.mkdir(parents=True, exist_ok=True)

    # Use unique filenames per worker (PID-based) to avoid conflicts
    worker_id = os.getpid()
    audio_filename = f"audio_{worker_id}.mp3"
    bg_filename = f"background_{worker_id}.jpg"
    lyrics_filename = f"lyrics_{worker_id}.json"

    # Copy assets with unique names
    shutil.copy2(str(audio_path), str(public_dir / audio_filename))
    shutil.copy2(str(lyrics_path), str(public_dir / lyrics_filename))
    print(f"Assets copied to {public_dir} (worker {worker_id})")

    # Generate background image
    song_name = Path(audio_path).stem
    lyrics_text = get_lyrics_text_from_json(str(lyrics_path))
    bg_image_path = str(public_dir / bg_filename)
    generate_background_image(song_name, lyrics_text, bg_image_path)

    # Load lyrics data for props
    with open(lyrics_path, 'r', encoding='utf-8') as f:
        lyrics_data = json.load(f)

    # Build props JSON for Remotion (tells it which files to use)
    props = json.dumps({
        "audioFile": audio_filename,
        "bgFile": bg_filename,
        "lyrics": lyrics_data
    })

    # Ensure output directory exists
    output_video_path = Path(output_video_path).resolve()
    output_video_path.parent.mkdir(parents=True, exist_ok=True)

    # Render with --props so each worker uses its own files
    render_cmd = (
        f'npx remotion render LyricVideo "{output_video_path}" '
        f"--concurrency=100% --log=error "
        f"--props='{props}'"
    )

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
    finally:
        # Clean up worker-specific files
        for f in [audio_filename, bg_filename, lyrics_filename]:
            p = public_dir / f
            if p.exists():
                p.unlink()


def render_video_ffmpeg(audio_path, lyrics_path, output_video_path):
    """
    Renders the lyric video using FFmpeg + Pillow (fast, ~1.5 min).
    Generates background image, then renders with ffmpeg_render.
    """
    print(f"\n--- Step 4: Rendering lyric video with FFmpeg ---")
    
    song_name = Path(audio_path).stem
    output_video_path = Path(output_video_path).resolve()
    output_video_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Generate background image in the output folder
    bg_image_path = str(output_video_path.parent / "background.jpg")
    lyrics_text = get_lyrics_text_from_json(str(lyrics_path))
    generate_background_image(song_name, lyrics_text, bg_image_path)
    
    # Render with FFmpeg
    success = ffmpeg_render_video(
        str(audio_path), str(lyrics_path),
        bg_image_path, str(output_video_path)
    )
    
    if success:
        print(f"--- SUCCESS: Video saved to {output_video_path} ---")
    else:
        print(f"FFmpeg render failed")
    
    return success


def main(audio_path, ground_truth_text=None, renderer="ffmpeg", nemo_client=None, **kwargs):
    """
    Main pipeline with per-step timing.
    
    Args:
        nemo_client: Optional NemoModelClient for shared server mode
    
    Returns:
        dict with keys:
          status: "success" | "failed"
          failed_at: step name if failed (e.g., "NeMo Alignment")
          error: error message if failed
          timings: {"extract_lyrics": 0.1, "punctuation": 5.2, "nemo_align": 28.3, "render": 210.5}
    """
    timings = {}
    result_info = {"status": "success", "failed_at": "", "error": "", "timings": timings}
    
    audio_file = Path(audio_path)
    if not audio_file.exists():
        result_info["status"] = "failed"
        result_info["failed_at"] = "Extract Lyrics"
        result_info["error"] = f"Audio file not found: {audio_path}"
        return result_info

    song_name = audio_file.stem

    # Create output directory
    output_base_dir = Path("output_song")
    song_output_dir = output_base_dir / song_name
    song_output_dir.mkdir(parents=True, exist_ok=True)

    print(f"--- Output will be saved to: {song_output_dir} ---")

    lyrics_file = song_output_dir / "lyrics.json"

    # ── Step 1: Extract Lyrics ──
    if not lyrics_file.exists():
        t0 = time.time()
        print(f"\n--- Step 1: Preparing Lyrics ---")
        
        if ground_truth_text:
            lyrics_text = ground_truth_text
            print(f"  Using provided ground truth lyrics ({len(lyrics_text.splitlines())} lines)")
        else:
            txt_path = _find_ground_truth_file(audio_file)
            if txt_path:
                raw_text = open(txt_path, "r", encoding="utf-8").read()
                lyrics_text = extract_lyrics_from_text(raw_text)
                print(f"  Extracted lyrics from: {txt_path.name}")
            else:
                result_info["status"] = "failed"
                result_info["failed_at"] = "Extract Lyrics"
                result_info["error"] = f"No lyrics .txt found for {song_name}"
                return result_info
        
        timings["extract_lyrics"] = round(time.time() - t0, 2)

        # ── Step 2: Add Punctuation ──
        t1 = time.time()
        print(f"\n--- Step 2: Adding Punctuation (Gemini) ---")
        try:
            punctuated = add_punctuation_with_gemini(lyrics_text)
            if punctuated:
                lyrics_text = punctuated
                print(f"  Punctuation added successfully")
        except Exception as e:
            print(f"  Punctuation failed: {e}. Continuing without.")
        timings["punctuation"] = round(time.time() - t1, 2)

        # ── Step 3: NeMo Forced Alignment ──
        t2 = time.time()
        print(f"\n--- Step 3: NeMo Forced Alignment ---")
        lyrics_tmp = lyrics_file.with_suffix(".tmp")
        
        try:
            align_result = align_with_nemo(
                str(audio_file),
                lyrics_text,
                str(lyrics_tmp),
                nemo_client=nemo_client
            )
        except Exception as e:
            align_result = None
            print(f"  NeMo error: {e}")
        
        if align_result:
            lyrics_tmp.rename(lyrics_file)
        else:
            # Fallback to Gemini
            print("  NeMo failed. Falling back to Gemini alignment...")
            try:
                from gemini_align import full_pipeline_gemini
                align_result = full_pipeline_gemini(str(audio_path), lyrics_text)
                if align_result:
                    lyrics_tmp = lyrics_file.with_suffix(".tmp")
                    with open(lyrics_tmp, 'w', encoding='utf-8') as f:
                        json.dump(align_result, f, ensure_ascii=False, indent=2)
                    lyrics_tmp.rename(lyrics_file)
                    print(f"  Gemini fallback: {len(align_result)} segments saved")
                else:
                    timings["nemo_align"] = round(time.time() - t2, 2)
                    result_info["status"] = "failed"
                    result_info["failed_at"] = "NeMo Alignment"
                    result_info["error"] = "Both NeMo and Gemini alignment failed"
                    return result_info
            except Exception as e:
                timings["nemo_align"] = round(time.time() - t2, 2)
                result_info["status"] = "failed"
                result_info["failed_at"] = "NeMo Alignment"
                result_info["error"] = f"NeMo + Gemini failed: {e}"
                return result_info
        
        timings["nemo_align"] = round(time.time() - t2, 2)
    else:
        print(f"\nLyrics already exist at: {lyrics_file}. Skipping alignment.")
        timings["extract_lyrics"] = 0
        timings["punctuation"] = 0
        timings["nemo_align"] = 0

    # ── Step 4: Render Video ──
    t3 = time.time()
    print(f"\n--- Step 4: Rendering Final Video ---")
    video_output = song_output_dir / f"{song_name}.mp4"
    
    # Acquire render semaphore if provided (limits concurrent FFmpeg encodes)
    render_semaphore = kwargs.get("render_semaphore")
    if render_semaphore:
        render_semaphore.acquire()
    
    try:
        if renderer == "remotion":
            render_ok = render_video_remotion(audio_file, lyrics_file, video_output)
        else:
            render_ok = render_video_ffmpeg(audio_file, lyrics_file, video_output)
    finally:
        if render_semaphore:
            render_semaphore.release()
    
    timings["render"] = round(time.time() - t3, 2)
    
    if not render_ok:
        result_info["status"] = "failed"
        result_info["failed_at"] = "Render Video"
        result_info["error"] = f"{renderer.capitalize()} render failed"
        return result_info

    print(f"\n{'='*60}")
    print(f"  ALL DONE! Your files are in: {song_output_dir}")
    print(f"  - lyrics.json  (timestamped lyrics)")
    print(f"  - {song_name}.mp4  (lyric video)")
    print(f"{'='*60}")
    
    return result_info


def _find_ground_truth_file(audio_path):
    """Find matching ground truth lyrics file."""
    audio_path = Path(audio_path)
    gt_dir = Path("ground_truth_lyrics")
    
    if not gt_dir.exists():
        return None
    
    candidates = [
        gt_dir / f"{audio_path.name}.txt",
        gt_dir / f"{audio_path.stem}.txt",
    ]
    
    for c in candidates:
        if c.exists():
            return c
    
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
    
    result = main(args.audio, ground_truth_text=ground_truth_text)
    if result:
        total = sum(result["timings"].values())
        print(f"\n📊 Timing: {result['timings']} | Total: {total:.1f}s")
