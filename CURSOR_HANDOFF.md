# Cursor Project Context & Recent Fixes

## Project Overview
**Audio to Video Maker**: A high-performance pipeline for creating lyric videos for spiritual songs (Bhajans). It converts MP3 audio + Text lyrics into 1080p MP4 videos with karaoke-style highlighting, AI-generated backgrounds, and automated Google Drive uploads.

## Tech Stack
- **Python 3.11+**
- **FFmpeg**: Video rendering with `hevc_videotoolbox` (Mac Hardware Acceleration).
- **Gemini 2.5 Flash**: Punctuation, Chorus detection, and Forced Alignment.
- **Rclone**: Automated upload to Google Drive.
- **WhisperX/Pyannote**: Voice Activity Detection (VAD).

---

## The "60-Second" Alignment Issue (FIXED)
### The Problem
Gemini's audio reasoning window is limited. When processing 3-5 minute songs in one shot, it often:
1. Lost track after `~60 seconds`.
2. Assigned huge time ranges (e.g., 40s) to single lines.
3. "Dumped" all remaining lyrics at the very end of the audio (crammed segments).

### The Solution (Implemented in `gemini_align.py`)
I have implemented a **Chunked Alignment Pipeline**:
1. **Audio Splitting**: The code now splits the MP3 into `~60s` WAV chunks temporarily using FFmpeg.
2. **Lyric Distribution**: Use VAD (Voice Activity Detection) to calculate "speech density" and distribute lyrics proportionally to each chunk.
3. **Parallelish-Serial Processing**: Each chunk is sent to Gemini separately with a subset of lyrics.
4. **Merging**: Timestamps are converted from "chunk-relative" to "absolute" and merged into a single `lyrics.json`.

---

## Critical Files for Cursor
1. `gemini_align.py`: The heart of the alignment logic.
   - `full_pipeline_gemini`: Handles chunking and merging.
   - `_validate_and_fix_segments`: Post-processor that detects crammed segments and redistributes them into empty VAD regions.
   - `_smooth_word_gaps`: Fills gaps between words to prevent karaoke flickering.
2. `ffmpeg_render.py`: High-performance HEVC rendering logic.
3. `batch_processor.py`: Orchestrates the flow and handles `rclone` uploads.

## Recent Major Updates (V3)
- **HEVC Encoding**: Reduced file size from ~120MB to **~35MB** per song with zero quality loss.
- **Thinking Mode**: Enabled `thinkingConfig` in Gemini 2.5 Flash for better reasoning about timestamps.
- **Rclone Move**: Automatically moves completed videos to `Bhajan Video Complete` on Google Drive and deletes local files to save space.
- **Hindu Deities Only**: Background images are strictly restricted to Hindu deities and architecture via specialized prompts in `generate_background.py`.

## Hand-off Instructions for Cursor
If you encounter alignment issues:
1. **Check `lyrics.json`**: Look for segments with duration `< 0.5s` or `> 10s`.
2. **VAD Alignment**: Ensure the speech regions detected by Pyannote are actually where the singing is.
3. **Chunk Overlap**: If a word is cut off at exactly 60s, 120s, or 180s, consider adding a 1-2s overlap between audio chunks.
