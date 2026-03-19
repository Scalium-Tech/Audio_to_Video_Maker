<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/Node.js-18+-339933?style=for-the-badge&logo=node.js&logoColor=white" alt="Node.js"/>
  <img src="https://img.shields.io/badge/Remotion-4.x-6C47FF?style=for-the-badge&logo=react&logoColor=white" alt="Remotion"/>
  <img src="https://img.shields.io/badge/FFmpeg-required-007808?style=for-the-badge&logo=ffmpeg&logoColor=white" alt="FFmpeg"/>
  <img src="https://img.shields.io/badge/NeMo-ASR-76B900?style=for-the-badge&logo=nvidia&logoColor=white" alt="NeMo"/>
</p>

<h1 align="center">🎵 LyricFlow</h1>

<p align="center">
  <strong>The Ultimate "Drop & Render" AI Lyric Video Pipeline.</strong>
</p>

<p align="center">
  <em>NeMo forced alignment for 40ms-precision timestamps, Gemini punctuation, and Remotion rendering.</em>
</p>

---

## ✨ How It Works

| Step | Engine | Time | Action |
| :--- | :--- | :--- | :--- |
| **1. Extract Lyrics** | `lyrics_extractor.py` | ~instant | Extracts clean Hindi lyrics from your `.txt` file |
| **2. Add Punctuation** | Gemini 2.5 Flash | ~5 sec | Adds `,` `!` `।` without changing any words |
| **3. NeMo Alignment** | NeMo CTC (`stt_hi_conformer_ctc_medium`) | ~30 sec | Forced alignment at **40ms precision** — word-level timestamps |
| **4. Render Video** | Remotion | ~3.5 min | Animates lyrics into a Full HD MP4 with background |

**Total: ~4.5 min per song**

---

## 🚀 Quick Start

1.  **Drop Songs**: Place `.mp3` files into `input_songs/`
2.  **Drop Lyrics**: Place matching `.txt` files into `ground_truth_lyrics/`
3.  **Run**:
    ```bash
    ./start
    ```

### Bulk Processing (500+ songs)

```bash
./start --workers 3   # 3 parallel workers
```

**Safeguards built-in:**
- ✅ Pre-flight validation (pairs mp3↔txt before starting)
- 🔒 Lock files (no duplicate processing)
- 📄 Atomic writes (no corrupted outputs)
- 📋 Per-song error logs
- 📊 Live progress dashboard (`output_song/progress.json`)

---

## 📂 Project Structure

```text
lyricflow/
├── input_songs/           # 🎵 Drop .mp3 here
├── ground_truth_lyrics/   # 📝 Drop matching .txt here
├── output_song/           # 🎥 Final MP4s and JSONs
├── start                  # 🚀 One-command launcher
├── batch_processor.py     # 🎯 Batch orchestrator with safeguards
├── main.py                # ⚙️ Core pipeline (NeMo + Gemini + Remotion)
├── nemo_align.py          # 🧠 NeMo CTC forced alignment
├── lyrics_extractor.py    # 📝 Lyrics extraction + Gemini punctuation
├── gemini_align.py        # 🔄 Gemini fallback alignment
├── generate_background.py # 🎨 AI background image generation
└── video/                 # 🎬 Remotion animation project
```

---

## 🛠️ Requirements & Setup

1.  **Python 3.11**: Required for NeMo alignment.
2.  **Dependencies**: `pip install -r requirements.txt` (includes `wakepy` for sleep prevention).
3.  **Environment**: Copy `.env.example` to `.env` and add your `GEMINI_API_KEY`.
4.  **FFmpeg**: Required for video rendering.
5.  **Rclone**: Required for Google Drive sync.

### 🏁 One-Click Setup (New!)
On a new machine, simply run:
```bash
python setup_check.py
```
This will verify your Python version, FFmpeg, Rclone, and automatically create your folders and `.env` file.

### 🚀 Running the Pipeline
Instead of the old Mac shell script, use the universal launcher:
```bash
python start.py
```
*This works the same on Windows, Mac, and Linux.*

---

## 📜 License

This project is open source and available under the [MIT License](LICENSE).

---

<p align="center">
  <strong>Made with ❤️ for Lyric Video Creators</strong>
  <br />
  <sub>Powered by NVIDIA NeMo for precision, Google Gemini for intelligence.</sub>
</p>
