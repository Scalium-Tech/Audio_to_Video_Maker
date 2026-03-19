import os
import sys
import json
import shutil
import subprocess
import argparse
import time
from dotenv import load_dotenv
load_dotenv()

from nemo_align import align_with_nemo
from lyrics_extractor import extract_lyrics_from_text, add_punctuation_with_gemini, sanitize_lyrics_text
from generate_background import generate_background_image, get_lyrics_text_from_json
from ffmpeg_render import render_with_ffmpeg as ffmpeg_render_video, get_last_render_error
from qa_validator import validate_song
from thumbnail_generator import generate_thumbnail
from metadata_generator import generate_metadata
from master_song_catalog import song_output_relpath, lookup_master_song

from pathlib import Path

_PIPELINE_ROOT = str(Path(__file__).parent.parent)
if _PIPELINE_ROOT not in sys.path:
    sys.path.insert(0, _PIPELINE_ROOT)

try:
    from failure_evidence import save_error_log, save_error_context
    _HAS_EVIDENCE = True
except ImportError:
    _HAS_EVIDENCE = False

# Path to the Remotion video project
VIDEO_PROJECT_DIR = Path(__file__).parent / "video"


def _probe_duration(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path)
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0


def _extend_tail_segment_to_audio(lyrics_file: Path, audio_file: Path, max_gap: float = 30.0) -> float:
    """
    Extend the last lyric segment so instrumental outros do not create a false
    QA tail-gap failure or leave the screen blank too early.
    Returns the number of seconds extended.
    """
    try:
        lyrics = json.loads(lyrics_file.read_text(encoding="utf-8"))
        if not isinstance(lyrics, list) or not lyrics:
            return 0.0

        audio_duration = _probe_duration(audio_file)
        if audio_duration <= 0:
            return 0.0

        last_segment = None
        for segment in reversed(lyrics):
            if isinstance(segment, dict) and float(segment.get("end", 0) or 0) > 0:
                last_segment = segment
                break

        if not last_segment:
            return 0.0

        last_end = float(last_segment.get("end", 0) or 0)
        gap = audio_duration - last_end
        if gap <= 0.5 or gap > max_gap:
            return 0.0

        new_end = max(last_end, audio_duration - 0.25)
        last_segment["end"] = round(new_end, 3)
        lyrics_file.write_text(json.dumps(lyrics, ensure_ascii=False, indent=2), encoding="utf-8")
        return round(new_end - last_end, 3)
    except Exception:
        return 0.0


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
    master_song = lookup_master_song(song_name)
    thumbnail_concept = master_song.get("Thumbnail Concept", "")
    generate_background_image(song_name, lyrics_text, bg_image_path, thumbnail_concept=thumbnail_concept)

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


def render_video_ffmpeg(audio_path, lyrics_path, output_video_path, preview_mode=False):
    """
    Renders the lyric video using FFmpeg + Pillow (fast, ~1.5 min).
    Generates background image, then renders with ffmpeg_render.
    """
    print(f"\n--- Step 4: Rendering lyric video with FFmpeg ---")
    
    song_name = Path(audio_path).stem
    output_video_path = Path(output_video_path).resolve()
    output_video_path.parent.mkdir(parents=True, exist_ok=True)
    
    # ── Stage checkpoint: reuse background if it already exists ──
    bg_image_path = str(output_video_path.parent / "background.jpg")
    if os.path.exists(bg_image_path) and os.path.getsize(bg_image_path) > 10000:
        print(f"  ♻️ Reusing existing background image")
    else:
        lyrics_text = get_lyrics_text_from_json(str(lyrics_path))
        # Fetch thumbnail concept from Master Excel
        master_song = lookup_master_song(song_name)
        thumbnail_concept = master_song.get("Thumbnail Concept", "")
        generate_background_image(song_name, lyrics_text, bg_image_path, thumbnail_concept=thumbnail_concept)
    
    # Preview mode: render only first 15 seconds
    max_duration = 15 if preview_mode else None
    
    # Render with FFmpeg
    success = ffmpeg_render_video(
        str(audio_path), str(lyrics_path),
        bg_image_path, str(output_video_path),
        max_duration=max_duration
    )
    
    if success:
        print(f"--- SUCCESS: Video saved to {output_video_path} ---")
    else:
        detail = get_last_render_error()
        if detail.get("message"):
            print(f"FFmpeg render failed [{detail.get('stage', 'unknown stage')}]: {detail['message']}")
        else:
            print(f"FFmpeg render failed")
    
    return success


def _write_error_log(song_output_dir: Path, failed_at: str, message: str, extra: dict | None = None):
    """Persist the full failure context for debugging later.

    Uses the shared failure_evidence module when available (writes both
    error.log + error_context.json); falls back to a simple JSON dump.
    """
    song_name = song_output_dir.name
    if _HAS_EVIDENCE:
        try:
            save_error_log(
                song_name, failed_at, message,
                extra_lines=[f"  {k}: {v}" for k, v in (extra or {}).items()
                             if isinstance(v, (str, int, float, bool))],
            )
            save_error_context(
                song_name, failed_at, message,
                last_known_state=failed_at,
                extra=extra,
            )
            return
        except Exception:
            pass

    payload = {
        "failed_at": failed_at,
        "message": message,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if extra:
        payload["details"] = extra
    try:
        (song_output_dir / "error.log").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def main(audio_path, ground_truth_text=None, renderer="ffmpeg", nemo_client=None, preview_mode=False, output_dir=None, **kwargs):
    """
    Main pipeline with per-step timing.
    
    Args:
        nemo_client: Optional NemoModelClient for shared server mode
        output_dir: Optional custom output directory path
    
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
    output_base_dir = Path(output_dir) if output_dir else Path("output_song")
    song_output_dir = output_base_dir / song_output_relpath(song_name)
    song_output_dir.mkdir(parents=True, exist_ok=True)

    print(f"--- Output will be saved to: {song_output_dir} ---")

    lyrics_file = song_output_dir / "lyrics.json"
    checkpoint_file = song_output_dir / "checkpoint.json"
    
    state = {}
    if checkpoint_file.exists():
        try:
            state = json.loads(checkpoint_file.read_text(encoding="utf-8"))
        except Exception:
            pass
            
    def save_state():
        checkpoint_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Step 1: Extract Lyrics ──
    if not lyrics_file.exists():
        t0 = time.time()
        print(f"\n--- Step 1: Preparing Lyrics ---")
        
        if "lyrics_text" in state:
            lyrics_text = state["lyrics_text"]
            print("  ⏭️ Resuming: Extracted lyrics loaded from checkpoint")
            timings["extract_lyrics"] = 0
        else:
            if ground_truth_text:
                lyrics_text = sanitize_lyrics_text(ground_truth_text)
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
                    _write_error_log(song_output_dir, result_info["failed_at"], result_info["error"])
                    return result_info
            
            state["lyrics_text"] = lyrics_text
            save_state()
            timings["extract_lyrics"] = round(time.time() - t0, 2)

        # ── Step 2: Add Punctuation ──
        t1 = time.time()
        print(f"\n--- Step 2: Adding Punctuation (Gemini) ---")
        
        if state.get("punctuated"):
            print("  ⏭️ Resuming: Punctuation already applied in checkpoint")
            timings["punctuation"] = 0
        else:
            try:
                punctuated = add_punctuation_with_gemini(lyrics_text)
                if punctuated:
                    lyrics_text = punctuated
                    print(f"  Punctuation added successfully")
                    state["lyrics_text"] = lyrics_text
                    state["punctuated"] = True
                    save_state()
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
                    _write_error_log(song_output_dir, result_info["failed_at"], result_info["error"],
                                     {"timings": timings})
                    return result_info
            except Exception as e:
                timings["nemo_align"] = round(time.time() - t2, 2)
                result_info["status"] = "failed"
                result_info["failed_at"] = "NeMo Alignment"
                result_info["error"] = f"NeMo + Gemini failed: {e}"
                _write_error_log(song_output_dir, result_info["failed_at"], result_info["error"],
                                 {"timings": timings, "exception": str(e)})
                return result_info
        
        timings["nemo_align"] = round(time.time() - t2, 2)
    else:
        print(f"\nLyrics already exist at: {lyrics_file}. Skipping alignment.")
        timings["extract_lyrics"] = 0
        timings["punctuation"] = 0
        timings["nemo_align"] = 0

    tail_extension = _extend_tail_segment_to_audio(lyrics_file, audio_file)
    if tail_extension > 0:
        print(f"  🎵 Extended final lyric hold by {tail_extension:.1f}s to cover the outro")

    # ── Step 4: Render Video ──
    t3 = time.time()
    print(f"\n--- Step 4: Rendering Final Video ---")
    # Preview: adjust output filename
    if preview_mode:
        video_output = song_output_dir / f"{song_name}_preview.mp4"
        print(f"  🎬 Preview mode: rendering first 15s → {video_output.name}")
    else:
        video_output = song_output_dir / f"{song_name}.mp4"
    
    # Acquire render semaphore if provided (limits concurrent FFmpeg encodes)
    render_semaphore = kwargs.get("render_semaphore")
    if render_semaphore:
        render_semaphore.acquire()
    
    try:
        if renderer == "remotion":
            render_ok = render_video_remotion(audio_file, lyrics_file, video_output)
        else:
            render_ok = render_video_ffmpeg(audio_file, lyrics_file, video_output, preview_mode=preview_mode)
    finally:
        if render_semaphore:
            render_semaphore.release()
    
    timings["render"] = round(time.time() - t3, 2)
    
    if not render_ok:
        render_detail = get_last_render_error()
        result_info["status"] = "failed"
        result_info["failed_at"] = "Render Video"
        stage = render_detail.get("stage") or "Render"
        detail_message = (render_detail.get("message") or "").strip()
        short_detail = detail_message[:220] + ("..." if len(detail_message) > 220 else "")
        result_info["error"] = (
            f"{renderer.capitalize()} render failed [{stage}]"
            + (f": {short_detail}" if short_detail else "")
        )
        render_detail["renderer"] = renderer
        render_detail["timings"] = timings
        _write_error_log(song_output_dir, result_info["failed_at"], result_info["error"], render_detail)
        return result_info
    
    # ── Step 5: Post-Render Validation ──
    if not preview_mode:
        validation_error = _validate_output(video_output, audio_file)
        if validation_error:
            result_info["status"] = "failed"
            result_info["failed_at"] = "Post-render Validation"
            result_info["error"] = validation_error
            _write_error_log(song_output_dir, result_info["failed_at"], result_info["error"])
            return result_info

        t4 = time.time()
        print(f"\n--- Step 6: QA Validation ---")
        qa_report = validate_song(
            song_name=song_name,
            audio_path=audio_file,
            lyrics_path=lyrics_file,
            video_path=video_output,
            background_path=song_output_dir / "background.jpg",
            output_dir=song_output_dir,
        )
        timings["qa_validation"] = round(time.time() - t4, 2)

        if qa_report["warnings"]:
            print("  QA warnings:")
            for warning in qa_report["warnings"]:
                print(f"    - {warning}")

        if qa_report["blocking_issues"]:
            result_info["status"] = "failed"
            result_info["failed_at"] = "QA Validation"
            result_info["error"] = "; ".join(qa_report["blocking_issues"])
            _write_error_log(song_output_dir, result_info["failed_at"], result_info["error"], qa_report)
            return result_info

        t5 = time.time()
        print(f"\n--- Step 7: Thumbnail Generation ---")
        thumbnail_path = song_output_dir / "thumbnail.jpg"
        try:
            generate_thumbnail(
                song_name=song_name,
                background_path=song_output_dir / "background.jpg",
                output_path=thumbnail_path,
            )
            timings["thumbnail"] = round(time.time() - t5, 2)
            print(f"  ✅ Thumbnail saved to {thumbnail_path.name}")
        except Exception as e:
            timings["thumbnail"] = round(time.time() - t5, 2)
            print(f"  ⚠️  Thumbnail generation failed: {e}")

        t6 = time.time()
        print(f"\n--- Step 8: Metadata Generation ---")
        try:
            metadata_path = generate_metadata(
                song_name=song_name,
                output_dir=song_output_dir,
                qa_report=qa_report,
            )
            timings["metadata"] = round(time.time() - t6, 2)
            print(f"  ✅ Metadata saved to {metadata_path.name}")
        except Exception as e:
            timings["metadata"] = round(time.time() - t6, 2)
            print(f"  ⚠️  Metadata generation failed: {e}")

    print(f"\n{'='*60}")
    print(f"  ALL DONE! Your files are in: {song_output_dir}")
    print(f"  - lyrics.json  (timestamped lyrics)")
    print(f"  - {song_name}.mp4  (lyric video)")
    print(f"{'='*60}")
    
    if checkpoint_file.exists():
        checkpoint_file.unlink()
    
    return result_info


def _validate_output(video_path, audio_path):
    """Post-render validation: check file size, streams, and duration match.
    
    Returns:
        None if valid, or error string if invalid.
    """
    video_path = Path(video_path)
    audio_path = Path(audio_path)
    
    # Check 1: file exists and minimum size (1 MB)
    if not video_path.exists():
        return f"Output file missing: {video_path.name}"
    
    size_mb = video_path.stat().st_size / (1024 * 1024)
    if size_mb < 1.0:
        return f"Output too small ({size_mb:.1f} MB < 1 MB). Likely corrupt."
    
    # Check 2: ffprobe validates video has audio + video streams
    try:
        probe_cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", str(video_path)
        ]
        probe_out = subprocess.check_output(probe_cmd).decode()
        import json as _json
        probe_data = _json.loads(probe_out)
        
        streams = probe_data.get("streams", [])
        has_video = any(s.get("codec_type") == "video" for s in streams)
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        
        if not has_video:
            return "Output MP4 has no video stream"
        if not has_audio:
            return "Output MP4 has no audio stream"
        
        # Check 3: duration match within ±2 seconds
        vid_duration = float(probe_data.get("format", {}).get("duration", 0))
        
        audio_probe = subprocess.check_output([
            "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)
        ]).decode().strip()
        audio_duration = float(audio_probe)
        
        diff = abs(vid_duration - audio_duration)
        if diff > 2.0:
            return f"Duration mismatch: video={vid_duration:.1f}s, audio={audio_duration:.1f}s (diff={diff:.1f}s)"
        
        print(f"  ✅ Post-render validation: {size_mb:.1f} MB, {vid_duration:.1f}s, video+audio OK")
        
    except subprocess.CalledProcessError:
        return "ffprobe failed on output video — file may be corrupt"
    except Exception as e:
        print(f"  ⚠️  Validation warning (non-fatal): {e}")
    
    return None


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
