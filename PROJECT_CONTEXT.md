# 🧠 LyricFlow — Project Context (AI Handoff File)

> **🔴 AI INSTRUCTIONS — READ CAREFULLY:**
> 1. This file is your **single source of truth**. Do NOT re-analyze the codebase.
> 2. **AUTO-UPDATE RULE**: After EVERY significant code change, install, or decision — immediately update the "Current Work In Progress" section and "Recent Changes Log" below. The user's session can end at ANY moment without warning.
> 3. Resume work from the "Current Work In Progress" section.

---

## 📋 Project Overview

**LyricFlow** is an AI-powered "Drop & Render" lyric video pipeline.

**What it does**: Takes an MP3 song → isolates vocals → transcribes/syncs lyrics → renders a Full HD lyric video (MP4).

**Tech Stack**: Python 3.x + Node.js + Remotion + FFmpeg + Gemini AI

### Pipeline (5 stages)
1. **Vocal Isolation** (`audio_utils.py`) — BS-Roformer model extracts clean vocals
2. **Transcription** (`transcribe_engine.py`) — WhisperX creates word-level timestamps
3. **AI Processing** (`text_refinery.py`) — Gemini refines/transliterates lyrics OR injects ground truth text
4. **Save** `lyrics.json` — Timestamped lyrics
5. **Render** (`video/src/LyricVideo.tsx`) — Remotion generates animated MP4

### Two Modes
- **Automated**: AI transcribes + renders (no text input needed)
- **Hybrid Injection**: You provide `.txt` lyrics, AI only handles timing → 100% accuracy

### Key Files
| File | Role |
|---|---|
| `main.py` | Core pipeline orchestrator |
| `batch_processor.py` | Batch mode — processes all MP3s from `input_songs/` |
| `audio_utils.py` | Vocal isolation (BS-Roformer + FFmpeg) |
| `transcribe_engine.py` | WhisperX transcription + alignment |
| `text_refinery.py` | Gemini AI lyric refinement / injection |
| `video/src/LyricVideo.tsx` | Remotion video component |
| `video/src/Root.tsx` | Remotion composition config |
| `.env` | Contains `GEMINI_API_KEY` |
| `requirements.txt` | Python dependencies |

### How to Run
```bash
# Single song:
python main.py input_songs/song.mp3 --language hi

# Batch (all songs in input_songs/):
python batch_processor.py --model large-v2
```

---

## 🔧 Environment Status

| Dependency | Status | Notes |
|---|---|---|
| Python | ⚠️ 3.14.0 | May need 3.10-3.12 for torch/whisperx compatibility |
| Node.js | ✅ v24.2.0 | |
| FFmpeg | ✅ 8.0.1 | |
| BS-Roformer Model | ✅ Downloaded | `models/` (~639MB) |
| Remotion node_modules | ✅ Installed | `video/node_modules/` |
| Gemini API Key | ✅ Set | In `.env` |
| Python packages | ❌ Mostly missing | Need `pip install -r requirements.txt` |

---

## 🚧 Current Work In Progress

<!-- 
UPDATE THIS SECTION before your session expires!
Format:
  - What you were working on
  - What's done
  - What's left
  - Any blockers or decisions pending
-->

### Active Task: Pipeline Operational — ✅ COMPLETE
- **Status**: Full pipeline tested and working
- **What's done**:
  - Python 3.11 venv with all dependencies (torch, whisperx, pyannote.audio)
  - Auto lyrics extractor from Suno AI txt files
  - Full pipeline run: vocal isolation → WhisperX → Gemini → Remotion
  - `./start` script for easy terminal execution
  - Render speed optimized with `--concurrency=100%`
  - Fixed interpolation crash for short-duration words
- **What's left**: None
- **Blockers**: None

### Recent Changes Log
<!-- Add entries here as you make changes, newest first -->
| Date | What Changed | Files Modified |
|---|---|---|
| 2025-02-25 | Full pipeline run, ./start script, render speed | `main.py`, `start` [NEW], `LyricVideo.tsx` |
| 2025-02-25 | Auto lyrics extractor from Suno AI txt | `lyrics_extractor.py` [NEW], `batch_processor.py` |
| 2025-02-25 | Devanagari output, punctuation, max 2 lines | `text_refinery.py`, `video/src/LyricVideo.tsx` |
| 2025-02-25 | Word-level karaoke red highlighting | `text_refinery.py`, `video/src/LyricVideo.tsx` |

---

## 📝 Design Decisions & Notes

<!-- Add important decisions, gotchas, or context here -->
- Language support: Hindi (`hi`), Marathi (`mr`), Punjabi (`pa`), Gujarati (`gu`), English (`en`)
- Default Whisper model: `large-v2`
- Ground truth lyrics go in `ground_truth_lyrics/` with same filename as the MP3
- Output goes to `output_song/<song_name>/` (lyrics.json + video MP4)
- The Remotion video has a dark gradient background with animated glow and progress bar

---

## 💡 Quick Commands

- **`/start`** — AI reads this file and resumes work automatically
- **`/save-progress`** — Manually force a save (but updates happen automatically after every action)
