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
| `lyrics_extractor.py` | Lyrics extraction + Gemini punctuation |
| `gemini_align.py` | Gemini fallback alignment |
| `generate_background.py` | AI background image generation |
| `video/src/LyricVideo.tsx` | Remotion video component |
| `.env` | Contains `GEMINI_API_KEY` |

### How to Run
```bash
# Batch (all songs in input_songs/):
./start

# With parallel workers:
./start --workers 3

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

### Active Task: Bulk Processing Safeguards — ✅ COMPLETE
- **Status**: All safeguards implemented
- **What's done**:
  - NeMo forced alignment replacing WhisperX (40ms precision)
  - Pre-flight validation (mp3↔txt pairing report)
  - Lock files (prevent duplicate processing)
  - Atomic writes (temp → rename)
  - Per-song error logs
  - Progress dashboard (`progress.json`)
- **What's left**: None
- **Blockers**: None

### Recent Changes Log
| Date | What Changed | Files Modified |
|---|---|---|
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
- Estimated: ~4.5 min per song (30s alignment + 3.5min rendering)

---

## 💡 Quick Commands

- **`/start`** — AI reads this file and resumes work automatically
- **`/save-progress`** — Manually force a save
