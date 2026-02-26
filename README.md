<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/Node.js-18+-339933?style=for-the-badge&logo=node.js&logoColor=white" alt="Node.js"/>
  <img src="https://img.shields.io/badge/Remotion-4.x-6C47FF?style=for-the-badge&logo=react&logoColor=white" alt="Remotion"/>
  <img src="https://img.shields.io/badge/FFmpeg-required-007808?style=for-the-badge&logo=ffmpeg&logoColor=white" alt="FFmpeg"/>
</p>

<h1 align="center">🎵 LyricFlow</h1>

<p align="center">
  <strong>The Ultimate "Drop & Render" AI Lyric Video Pipeline.</strong>
</p>

<p align="center">
  <em>Automated vocal isolation, precise phonetic sync, and intelligent text injection for multilingual songs.</em>
</p>

---

## ✨ Core Workflows

LyricFlow now supports two powerful ways to create videos:

### 1. Fully Automated Mode (Whisper AI)
AI hears the song, writes the lyrics, transliterates them, and renders the video.
*   **Best for:** Quick videos where you don't have the text ready.

### 2. Hybrid "Injection" Mode (Ground Truth)
AI listens for timing, but uses **your provided text** for 100% accuracy.
*   **Best for:** Professional releases, non-Latin scripts (Marathi, Hindi, Gujarati), and zero-hallucination results.

---

## 🚀 Quick Start (Batch Mode)

The most efficient way to use LyricFlow is the **Batch Processor**.

1.  **Drop Songs**: Place your `.mp3` files into the `input_songs/` folder.
2.  **Drop Lyrics (Optional)**: If you have the correct text, place a `.txt` file with the **same name** into `ground_truth_lyrics/`.
3.  **Run Batch**:
    ```bash
    python batch_processor.py --model large-v2
    ```

LyricFlow will sequence through every song, isolate vocals, sync the lyrics (autodetecting Injection Mode if the `.txt` exists), and render final videos.

---

## 🏗️ The "Injection" Architecture

| Step | Engine | Action |
| :--- | :--- | :--- |
| **1. Vocal Isolation** | BS-Roformer | Extracts clean voice for the AI to "hear" clearly. |
| **2. Timing Shell** | WhisperX / MMS | Creates a `timing_shell.json` — purely tracking *when* sounds happen. |
| **3. Text Injection** | Gemini 2.5 Flash | Maps your Ground Truth text onto the AI's Timing Shell phonetically. |
| **4. Rendering** | Remotion | Animates the injected text into a stunning Full HD MP4. |

---

## 📂 Project Structure

```text
lyricflow/
├── input_songs/           # 🎵 AUDIO INPUT: Drop .mp3 here
├── ground_truth_lyrics/   # 📝 TEXT INPUT: Drop matching .txt here
├── output_song/           # 🎥 VIDEO OUTPUT: Final MP4s and JSONs
├── batch_processor.py     # 🎯 The "Drop & Render" Orchestrator
├── main.py                # ⚙️ Core Pipeline Logic
├── transcribe_engine.py   # 📝 Timing & Sync Engine
├── text_refinery.py       # 🤖 Gemini Injection Logic
└── video/                 # 🎬 Remotion Animation Project
```

---

## 🛠️ Requirements & Setup

1.  **Environment**: Create a `.env` file with `GEMINI_API_KEY=your_key`.
2.  **Node.js**: Run `cd video && npm install` to set up the renderer.
3.  **Python**: Run `pip install -r requirements.txt`.
4.  **FFmpeg**: Ensure FFmpeg is installed in your system PATH.

---

## 📜 License

This project is open source and available under the [MIT License](LICENSE).

---

<p align="center">
  <strong>Made with ❤️ for Lyric Video Creators</strong>
  <br />
  <sub>Automating the art of synchronization.</sub>
</p>
