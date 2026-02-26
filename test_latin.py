
import json
import os
from pathlib import Path
from text_refinery import refine_lyrics_with_gemini
from dotenv import load_dotenv

load_dotenv()

raw_path = r"c:\Users\nakul\OneDrive\Desktop\best_video_maker\output_song\Maajha Gaav (My Village)\raw_segments.json"
with open(raw_path, "r", encoding="utf-8") as f:
    raw_segments = json.load(f)

print("Attempting refinement...", flush=True)
result = refine_lyrics_with_gemini(raw_segments, language="mr")
if result:
    print("SUCCESS")
    print("Sample Text:", result[0]['text'])
else:
    print("FAILED")
