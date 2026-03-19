# 🧠 LyricFlow — Project Context (AI Handoff File)

> **🔴 AI INSTRUCTIONS — READ CAREFULLY:**
> 1. This file is your **single source of truth**. Do NOT re-analyze the codebase.
> 2. **AUTO-UPDATE RULE**: After EVERY significant code change, install, or decision — immediately update the "Current Work In Progress" section and "Recent Changes Log" below. The user's session can end at ANY moment without warning.
> 3. Resume work from the "Current Work In Progress" section.

---

## 📋 Project Overview

**LyricFlow** is an AI-powered "Drop & Render" lyric video pipeline.

**What it does**: Takes an MP3 song + lyrics text → NeMo forced alignment (40ms precision) → renders a Full HD lyric video (MP4).

**Tech Stack**: Python 3.11 + Node.js + Remotion + FFmpeg + NeMo ASR + Gemini AI

### Pipeline (4 stages)
1. **Extract Lyrics** (`lyrics_extractor.py`) — Extracts clean Hindi lyrics from txt file
2. **Add Punctuation** (`lyrics_extractor.py`) — Gemini adds `,` `!` `।` without changing words
3. **NeMo Alignment** (`nemo_align.py`) — CTC forced alignment at 40ms precision, word-level timestamps
4. **Render** (`video/src/LyricVideo.tsx`) — Remotion generates animated MP4

### Key Files
| File | Role |
|---|---|
| `start` | One-command launcher (`./start`) |
| `main.py` | Core pipeline orchestrator |
| `batch_processor.py` | Batch mode with safeguards (validation, locks, progress) |
| `nemo_align.py` | NeMo CTC forced alignment (Hindi `stt_hi_conformer_ctc_medium`) |
| `nemo_server.py` | Shared NeMo model server for parallel workers |
| `lyrics_extractor.py` | Lyrics extraction + Gemini punctuation |
| `gemini_align.py` | Gemini fallback alignment |
| `generate_background.py` | AI background image generation |
| `ffmpeg_render.py` | Optimized renderer (half-res + VideoToolbox HW encoding) |
| `video/src/LyricVideo.tsx` | Remotion video component |
| `.env` | Contains `GEMINI_API_KEY` |

### How to Run
```bash
# Batch (all songs in input_songs/):
./start

# With parallel workers + render throttle:
./start --workers 20 --max-render-workers 6

# With auto-retry (default 3 retries, configurable):
./start --workers 20 --max-retries 5

# Single song:
python3.11 nemo_align.py input_songs/song.mp3 ground_truth_lyrics/song.mp3.txt
```

---

## 🔧 Environment Status

| Dependency | Status | Notes |
|---|---|---|
| Python | ✅ 3.11 | Required for NeMo |
| NeMo | ✅ Installed | `nemo_toolkit[asr]` with `stt_hi_conformer_ctc_medium` |
| Node.js | ✅ v24.2.0 | |
| FFmpeg | ✅ 8.0.1 | |
| Remotion | ✅ Installed | `video/node_modules/` |
| Gemini API Key | ✅ Set | In `.env` |

---

## 🚧 Current Work In Progress

### Active Task: Re-processing Full Batch — ✅ READY
- **Status**: Storage optimized (Direct Drive upload + Auto-cleanup).
- **What's done**:
  1. **Patched Bugs**: Fixed output nesting, environment loading, and upload paths.
  2. **Storage Optimization**: Removed local Desktop copy; outputs now upload directly to Drive.
  3. **Auto-Cleanup**: Song folders are deleted from `output_song/` immediately after successful upload.
  4. **New Feature**: Added original `.txt` lyrics copy to the output folder.
- **What's next**: Run `./start --workers 15` to process all 1,500 songs.
- **Blockers**: None.

### Recent Changes Log
| Date | What Changed | Files Modified |
|---|---|---|
| 2026-03-13 | Reverted to flat folder structure (removed date-based subfolders) and added original .txt copy to output | `batch_processor.py` |
| 2026-03-13 | Optimized storage: removed local Desktop copy and added auto-cleanup after Drive upload | `upload_queue.py`, `batch_processor.py` |
| 2026-03-13 | Fixed double folder nesting and environment variable loading order for uploads | `batch_processor.py` |
| 2026-03-13 | V4.5: date folders, config hot-reload, batch scheduling, smart resume, multi-machine queue, health check endpoint | `main.py`, `batch_processor.py`, `start`, `machine_queue.py` [NEW], `health_server.py` [NEW] |
| 2026-03-13 | V4.4: preview mode, background reuse checkpoint, post-render validation | `main.py`, `ffmpeg_render.py`, `batch_processor.py`, `start` |
| 2026-03-13 | V4.3: queue priority, gemini cache, dashboard, pipeline mode, ffmpeg progress, memory check, HTML report, audio normalization, dedup, process pool | `batch_processor.py`, `gemini_utils.py`, `ffmpeg_render.py`, `start`, `gemini_cache.py` [NEW], `report_generator.py` [NEW], `dashboard.py` [NEW] |
| 2026-03-13 | V4.2: config.yaml, ffprobe validation, stale lock cleanup, per-song retry, token tracking, dry-run, rate-limit observability | `config.yaml` [NEW], `batch_processor.py`, `gemini_utils.py`, `start` |
| 2026-03-13 | V4.1: per-endpoint key pools, parallel uploads, lyrics_extractor bug fix | `gemini_utils.py`, `upload_queue.py`, `batch_processor.py`, `lyrics_extractor.py`, `gemini_align.py`, `generate_background.py`, `.env.example` |
| 2026-03-13 | V4 pipeline improvements: multi-key round robin, NeMo auto-restart, checkpointing, retry loop | `gemini_utils.py`, `nemo_server.py`, `batch_processor.py`, `start`, `.env.example` |
| 2026-03-13 | Pushed project to GitHub V4 repository with updated .gitignore | `.gitignore`, `PROJECT_CONTEXT.md` |
| 2026-03-12 | Analyzed 518-song batch logs, generated performance report and scaling strategy | `PROJECT_CONTEXT.md` |
| 2026-03-02 | Gemini chunked alignment: 60s chunks, VAD distribution, math import fix, smooth word gaps, validate/fix segments | `gemini_align.py` |
| 2026-02-27 | Performance optimizations for 20 workers | `ffmpeg_render.py`, `nemo_server.py` [NEW], `nemo_align.py`, `batch_processor.py`, `main.py`, `start` |
| 2026-02-27 | Bulk processing safeguards | `batch_processor.py` |
| 2026-02-27 | NeMo alignment replacing WhisperX | `nemo_align.py` [NEW], `main.py`, `start` |
| 2026-02-27 | Gemini punctuation integration | `lyrics_extractor.py`, `nemo_align.py` |
| 2025-02-25 | Full pipeline run, ./start script | `main.py`, `start` [NEW] |

---

## 📝 Design Decisions & Notes

- NeMo model: `stt_hi_conformer_ctc_medium` (Hindi CTC, ~100MB, cached at `~/.cache/torch/NeMo/`)
- Alignment precision: 40ms per frame
- Gemini only used for: punctuation + background image generation + fallback alignment
- Ground truth lyrics go in `ground_truth_lyrics/` with filename `<mp3_name>.txt`
- Output goes to `output_song/<song_name>/` (lyrics.json + video MP4)
- Internal render: 960×540, upscaled to 1920×1080 via FFmpeg Lanczos filter
- Encoder: `h264_videotoolbox` (hardware) with `libx264` fallback
- Shared NeMo server: single process loads model, workers request log-probs via queues
- NeMo server: 300s timeouts, health-check pings, auto-restart up to 3 times
- Gemini API: Per-endpoint key pools with per-key 60s cooldown on 429 errors
- Parallel rclone uploads via background thread (non-blocking delivery)
- Retry loop: `start` script re-runs batch processor if failures remain (max 3 retries)
- Estimated: ~4.5 min per song (30s alignment + 3.5min rendering)

---

## 💡 Quick Commands

- **`/start`** — AI reads this file and resumes work automatically
- **`/save-progress`** — Manually force a save
