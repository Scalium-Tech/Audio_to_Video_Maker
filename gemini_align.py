"""
Gemini Forced Alignment Module
=============================
Sends audio + lyrics text to Gemini's multimodal API to get precise 
word-level timestamps, replacing WhisperX's unreliable Hindi alignment.
"""

import os
import json
import re
import base64
import argparse
from pathlib import Path

def align_lyrics_with_gemini(audio_path, lyrics_segments, api_key=None):
    """
    Send audio + lyrics to Gemini for precise word-level timestamps.
    
    Args:
        audio_path: Path to the audio file (MP3/WAV)
        lyrics_segments: List of lyric segments with text/start/end
        api_key: Gemini API key (or uses GEMINI_API_KEY env var)
    
    Returns:
        Updated lyrics_segments with accurate word timestamps
    """
    import requests
    
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("WARNING: No GEMINI_API_KEY found. Skipping forced alignment.")
        return lyrics_segments
    
    # Read audio file
    audio_path = Path(audio_path)
    if not audio_path.exists():
        print(f"WARNING: Audio file not found: {audio_path}")
        return lyrics_segments
    
    audio_bytes = audio_path.read_bytes()
    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    
    # Determine mime type
    ext = audio_path.suffix.lower()
    mime_map = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".ogg": "audio/ogg"}
    mime_type = mime_map.get(ext, "audio/mpeg")
    
    # Build the lyrics text with segment timing hints
    lyrics_text = ""
    for i, seg in enumerate(lyrics_segments):
        lyrics_text += f"Segment {i}: [{seg['start']:.2f}s - {seg['end']:.2f}s] \"{seg['text']}\"\n"
    
    prompt = f"""You are an audio-to-word timestamp alignment tool. I'm giving you an audio file and its EXACT lyrics with FIXED segment timing.

CRITICAL RULES:
- DO NOT change segment start/end times — they are FIXED
- DO NOT change the text — it is EXACT
- ONLY provide word-level timestamps WITHIN each segment's time boundaries
- Each word's start/end MUST be between the segment's start and end times

LYRICS WITH FIXED TIMING:
{lyrics_text}

For each segment, provide word-level timestamps by listening to the audio.
Return a JSON array where each element has:
- "seg_index": the segment number (0-based)
- "words": array of {{"word": "...", "start": X.XX, "end": X.XX}}

Example format:
[
  {{"seg_index": 0, "words": [{{"word": "first", "start": 22.90, "end": 23.30}}, ...]}},
  ...
]

Return ONLY the JSON array:"""

    # Try multiple models
    models = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
    ]
    
    for model_name in models:
        print(f"  Attempting forced alignment with {model_name}...", flush=True)
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        
        payload = {
            "contents": [{
                "parts": [
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": audio_b64
                        }
                    },
                    {"text": prompt}
                ]
            }],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 65536,
            }
        }
        
        try:
            response = requests.post(url, json=payload, timeout=180)
            
            if response.status_code != 200:
                print(f"  {model_name} failed: HTTP {response.status_code}", flush=True)
                continue
            
            result = response.json()
            
            # Gemini 2.5 Flash may return multiple parts (thinking + text)
            parts = result["candidates"][0]["content"]["parts"]
            text_response = ""
            for part in parts:
                if "text" in part:
                    text_response = part["text"]
                    if "[" in text_response and "]" in text_response:
                        break
            
            # Parse JSON from response — strip markdown code blocks if present
            clean = text_response.strip()
            clean = re.sub(r'^```(?:json)?\s*', '', clean)
            clean = re.sub(r'\s*```$', '', clean)
            clean = clean.strip()
            
            # Find JSON array
            start_idx = clean.find("[")
            end_idx = clean.rfind("]") + 1
            
            if start_idx == -1 or end_idx == 0:
                print(f"  {model_name}: No JSON array found in response", flush=True)
                print(f"  Response preview: {clean[:200]}...", flush=True)
                continue
            
            json_str = clean[start_idx:end_idx]
            aligned_data = json.loads(json_str)
            
            if not isinstance(aligned_data, list) or len(aligned_data) == 0:
                print(f"  {model_name}: Empty or invalid response", flush=True)
                continue
            
            # ── Merge Gemini word timestamps back into original segments ──
            # Build a lookup: seg_index -> words
            word_map = {}
            for item in aligned_data:
                idx = item.get("seg_index", item.get("segment", -1))
                words = item.get("words", [])
                if idx >= 0 and words:
                    word_map[idx] = words
            
            # Apply to original segments, keeping original boundaries
            updated_count = 0
            for i, seg in enumerate(lyrics_segments):
                if i in word_map:
                    gemini_words = word_map[i]
                    seg_start = seg["start"]
                    seg_end = seg["end"]
                    
                    # Clamp word timestamps into segment boundaries
                    clamped_words = []
                    for w in gemini_words:
                        ws = max(seg_start, min(w["start"], seg_end))
                        we = max(ws + 0.05, min(w["end"], seg_end))
                        # Cap word duration to 1.5s
                        if we - ws > 1.5:
                            we = round(ws + 1.5, 2)
                        clamped_words.append({
                            "word": w["word"],
                            "start": round(ws, 2),
                            "end": round(we, 2)
                        })
                    
                    # Sort by start time
                    clamped_words.sort(key=lambda w: w["start"])
                    seg["words"] = clamped_words
                    updated_count += 1
            
            print(f"  SUCCESS: Updated {updated_count}/{len(lyrics_segments)} segments with Gemini word timing", flush=True)
            
            # Show sample
            for seg in lyrics_segments[:3]:
                print(f"    [{seg['start']:.1f}-{seg['end']:.1f}s] {seg['text'][:40]}...")
                for w in seg.get("words", [])[:4]:
                    print(f"      {w['start']:.2f}-{w['end']:.2f}: \"{w['word']}\"")
            
            return lyrics_segments
            
        except json.JSONDecodeError as e:
            print(f"  {model_name}: JSON parse error: {e}", flush=True)
        except Exception as e:
            print(f"  {model_name}: Error: {e}", flush=True)
    
    print("  All models failed for forced alignment. Keeping original timestamps.", flush=True)
    return lyrics_segments


def align_and_split_lyrics(audio_path, lyrics_segments, api_key=None):
    """
    MERGED: Chorus detection + word-level alignment in ONE Gemini call.
    Saves ~60s per song by avoiding a second audio upload.
    
    1. Detects how many times each line repeats (chorus splitting)
    2. Provides word-level timestamps for alignment
    
    Returns: Updated lyrics_segments with splits and word timestamps.
    """
    import requests
    
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("  WARNING: No GEMINI_API_KEY. Skipping alignment.")
        return lyrics_segments
    
    audio_path = Path(audio_path)
    if not audio_path.exists():
        print(f"  WARNING: Audio file not found: {audio_path}")
        return lyrics_segments
    
    audio_bytes = audio_path.read_bytes()
    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    
    ext = audio_path.suffix.lower()
    mime_map = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}
    mime_type = mime_map.get(ext, "audio/mpeg")
    
    # Build segment info
    seg_info = ""
    for i, seg in enumerate(lyrics_segments):
        seg_info += f'Segment {i}: [{seg["start"]:.2f}s - {seg["end"]:.2f}s] "{seg["text"]}"\n'
    
    prompt = f"""You are an audio-to-lyrics alignment tool. I'm giving you an audio file and lyrics with FIXED segment timing.

LYRICS WITH FIXED TIMING:
{seg_info}

DO TWO THINGS for each segment:

1. COUNT REPETITIONS: How many times is the text actually sung in that time range?
   - If sung once, repetitions = 1
   - If it's a repeated chorus/refrain sung 2-4+ times, give the actual count

2. WORD TIMESTAMPS: For the FIRST occurrence of the text in each segment, provide word-level timestamps.
   - Word start/end MUST be within the segment's time boundaries
   - Words must be chronological

Return a JSON array where each element has:
- "seg_index": segment number (0-based)
- "repetitions": how many times the line is sung (1 if not repeated)
- "words": array of {{"word": "...", "start": X.XX, "end": X.XX}} for the FIRST occurrence

Example:
[
  {{"seg_index": 0, "repetitions": 1, "words": [{{"word": "hello", "start": 1.0, "end": 1.5}}]}},
  {{"seg_index": 1, "repetitions": 4, "words": [{{"word": "chorus", "start": 5.0, "end": 5.5}}]}}
]

Return ONLY the JSON array:"""

    models = ["gemini-2.5-flash", "gemini-2.0-flash"]
    
    for model_name in models:
        print(f"  Attempting merged align+split with {model_name}...", flush=True)
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        
        payload = {
            "contents": [{"parts": [
                {"inlineData": {"mimeType": mime_type, "data": audio_b64}},
                {"text": prompt}
            ]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 65536,
            }
        }
        
        try:
            response = requests.post(url, json=payload, timeout=300)
            
            if response.status_code != 200:
                print(f"  {model_name} failed: HTTP {response.status_code}", flush=True)
                continue
            
            result = response.json()
            parts = result["candidates"][0]["content"]["parts"]
            all_texts = [p["text"] for p in parts if "text" in p]
            text_response = all_texts[-1] if all_texts else ""
            
            if not text_response:
                print(f"  {model_name}: No text in response", flush=True)
                continue
            
            # Parse JSON — handle code fences
            try:
                data = json.loads(text_response.strip())
            except json.JSONDecodeError:
                stripped = text_response.replace("```json", "").replace("```", "").strip()
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError:
                    idx_s = stripped.find("[")
                    idx_e = stripped.rfind("]")
                    if idx_s >= 0 and idx_e > idx_s:
                        data = json.loads(stripped[idx_s:idx_e+1])
                    else:
                        print(f"  {model_name}: No JSON found", flush=True)
                        continue
            
            if not isinstance(data, list) or len(data) == 0:
                print(f"  {model_name}: Empty response", flush=True)
                continue
            
            # Build lookup
            seg_data = {}
            for item in data:
                idx = item.get("seg_index", -1)
                if idx >= 0:
                    seg_data[idx] = item
            
            # Process: split repetitions + apply word timestamps
            expanded = []
            for i, seg in enumerate(lyrics_segments):
                info = seg_data.get(i, {})
                reps = max(1, min(info.get("repetitions", 1), 10))
                gemini_words = info.get("words", [])
                
                if reps > 1:
                    # Split into repetitions
                    dur = seg["end"] - seg["start"]
                    rep_dur = dur / reps
                    print(f"  Seg {i}: \"{seg['text'][:35]}\" → {reps}x ({rep_dur:.1f}s each)", flush=True)
                    
                    for r in range(reps):
                        rep_start = round(seg["start"] + r * rep_dur, 2)
                        rep_end = round(seg["start"] + (r + 1) * rep_dur, 2)
                        
                        if r == 0 and gemini_words:
                            # Use Gemini words for first repetition, clamped
                            words = _clamp_words(gemini_words, rep_start, rep_end)
                        else:
                            # Even distribution for subsequent repetitions
                            words = _even_words(seg["text"], rep_start, rep_end)
                        
                        expanded.append({
                            "text": seg["text"],
                            "start": rep_start,
                            "end": rep_end,
                            "words": words
                        })
                else:
                    # Single occurrence — apply Gemini word timestamps
                    if gemini_words:
                        seg["words"] = _clamp_words(gemini_words, seg["start"], seg["end"])
                    expanded.append(seg)
            
            print(f"  SUCCESS: {len(lyrics_segments)} → {len(expanded)} segments ({model_name})", flush=True)
            
            # Show sample
            for seg in expanded[:2]:
                print(f"    [{seg['start']:.1f}-{seg['end']:.1f}s] {seg['text'][:40]}...")
                for w in seg.get("words", [])[:3]:
                    print(f"      {w['start']:.2f}-{w['end']:.2f}: \"{w['word']}\"")
            
            return expanded
            
        except json.JSONDecodeError as e:
            print(f"  {model_name}: JSON parse error: {e}", flush=True)
        except Exception as e:
            print(f"  {model_name}: Error: {e}", flush=True)
    
    print("  All models failed. Keeping original segments.", flush=True)
    return lyrics_segments


def _clamp_words(gemini_words, seg_start, seg_end):
    """Clamp word timestamps into segment boundaries and cap duration."""
    clamped = []
    for w in gemini_words:
        ws = max(seg_start, min(w["start"], seg_end))
        we = max(ws + 0.05, min(w["end"], seg_end))
        if we - ws > 1.5:
            we = round(ws + 1.5, 2)
        clamped.append({"word": w["word"], "start": round(ws, 2), "end": round(we, 2)})
    clamped.sort(key=lambda w: w["start"])
    return clamped


def _even_words(text, start, end):
    """Create evenly distributed word timestamps."""
    text_words = text.replace(",", " ").replace("।", " ").replace("!", " ").split()
    text_words = [w.strip() for w in text_words if w.strip()]
    n = max(len(text_words), 1)
    dur = end - start
    slot = dur / n
    return [{"word": tw, "start": round(start + j * slot, 2),
             "end": round(start + (j + 1) * slot - 0.03, 2)}
            for j, tw in enumerate(text_words)]


def detect_chorus_repetitions(audio_path, lyrics_segments, api_key=None):
    """
    Use Gemini to detect how many times each line is actually repeated in the audio.
    Long segments that contain repeated verses get split into the correct number of repetitions.
    
    Args:
        audio_path: Path to the audio file
        lyrics_segments: List of lyric segments with text/start/end
        api_key: Gemini API key
    
    Returns:
        Updated lyrics_segments with long segments split into correct repetition count
    """
    import requests
    
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("  WARNING: No GEMINI_API_KEY. Skipping repetition detection.")
        return lyrics_segments
    
    audio_path = Path(audio_path)
    if not audio_path.exists():
        print(f"  WARNING: Audio file not found: {audio_path}")
        return lyrics_segments
    
    audio_bytes = audio_path.read_bytes()
    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    
    ext = audio_path.suffix.lower()
    mime_map = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}
    mime_type = mime_map.get(ext, "audio/mpeg")
    
    # Build segment info for Gemini
    seg_info = ""
    for i, seg in enumerate(lyrics_segments):
        dur = seg["end"] - seg["start"]
        seg_info += f'Segment {i}: [{seg["start"]:.1f}s-{seg["end"]:.1f}s] ({dur:.1f}s) "{seg["text"]}"\n'
    
    prompt = f"""Listen to this audio carefully. I have lyrics segments with timing below.
Some segments may contain a line that is REPEATED multiple times (chorus/refrain).

SEGMENTS:
{seg_info}

For EACH segment, count how many times the text is actually sung in that time range.
- If the line is sung once, count = 1
- If the line is a repeated chorus sung 2, 3, 4, or more times in that time range, give the actual count
- Pay attention to how many distinct vocal repetitions you hear in that segment's time range

Return a JSON array with one object per segment:
[
  {{"seg_index": 0, "repetitions": 1}},
  {{"seg_index": 1, "repetitions": 1}},
  {{"seg_index": 2, "repetitions": 2}},
  ...
]

Return ONLY the JSON array:"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    payload = {
        "contents": [{"parts": [
            {"inlineData": {"mimeType": mime_type, "data": audio_b64}},
            {"text": prompt}
        ]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 4096,
        }
    }
    
    print("  Detecting chorus repetitions with Gemini...", flush=True)
    
    try:
        response = requests.post(url, json=payload, timeout=180)
        
        if response.status_code != 200:
            print(f"  Failed: HTTP {response.status_code}", flush=True)
            return lyrics_segments
        
        result = response.json()
        
        # Collect ALL text from all parts
        parts = result["candidates"][0]["content"]["parts"]
        all_texts = [p["text"] for p in parts if "text" in p]
        # Use the last text part (thinking block is usually first)
        text_response = all_texts[-1] if all_texts else ""
        
        if not text_response:
            print(f"  No text found in response (parts: {len(parts)})", flush=True)
            return lyrics_segments
        
        # Extract JSON array — handle ```json ... ``` wrapping
        try:
            # Try direct JSON parse first
            rep_data = json.loads(text_response.strip())
        except json.JSONDecodeError:
            # Strip code fences and find JSON array
            stripped = text_response.replace("```json", "").replace("```", "").strip()
            try:
                rep_data = json.loads(stripped)
            except json.JSONDecodeError:
                # Last resort: find [ and ] and extract
                idx_start = stripped.find("[")
                idx_end = stripped.rfind("]")
                if idx_start >= 0 and idx_end > idx_start:
                    try:
                        rep_data = json.loads(stripped[idx_start:idx_end+1])
                    except json.JSONDecodeError as e:
                        print(f"  JSON parse failed: {e}", flush=True)
                        print(f"  Text preview: {stripped[:200]}", flush=True)
                        return lyrics_segments
                else:
                    print(f"  No JSON array found in response", flush=True)
                    return lyrics_segments
        
        # Build repetition map
        rep_map = {}
        for item in rep_data:
            idx = item.get("seg_index", -1)
            reps = item.get("repetitions", 1)
            if idx >= 0:
                rep_map[idx] = max(1, min(reps, 10))  # clamp 1-10
        
        # Split segments that have repetitions > 1
        expanded = []
        for i, seg in enumerate(lyrics_segments):
            reps = rep_map.get(i, 1)
            if reps > 1:
                dur = seg["end"] - seg["start"]
                rep_dur = dur / reps
                print(f"  Seg {i}: \"{seg['text'][:40]}\" → {reps} repetitions ({rep_dur:.1f}s each)", flush=True)
                for r in range(reps):
                    rep_start = round(seg["start"] + r * rep_dur, 2)
                    rep_end = round(seg["start"] + (r + 1) * rep_dur, 2)
                    # Create even word timestamps within each repetition
                    text_words = seg["text"].replace(",", " ").replace("।", " ").split()
                    text_words = [w.strip() for w in text_words if w.strip()]
                    n = len(text_words)
                    word_slot = rep_dur / max(n, 1)
                    words = [{"word": tw, "start": round(rep_start + j * word_slot, 2), 
                              "end": round(rep_start + (j + 1) * word_slot - 0.03, 2)}
                             for j, tw in enumerate(text_words)]
                    expanded.append({
                        "text": seg["text"],
                        "start": rep_start,
                        "end": rep_end,
                        "words": words
                    })
            else:
                expanded.append(seg)
        
        print(f"  Done: {len(lyrics_segments)} segments → {len(expanded)} segments", flush=True)
        return expanded
        
    except Exception as e:
        print(f"  Repetition detection failed: {e}", flush=True)
        return lyrics_segments


def main():
    """Standalone CLI for testing forced alignment."""
    parser = argparse.ArgumentParser(description="Gemini Forced Alignment")
    parser.add_argument("--audio", required=True, help="Path to audio file")
    parser.add_argument("--lyrics", required=True, help="Path to lyrics.json")
    parser.add_argument("--output", help="Output path (default: overwrites lyrics)")
    args = parser.parse_args()
    
    from dotenv import load_dotenv
    load_dotenv()
    
    with open(args.lyrics, 'r', encoding='utf-8') as f:
        lyrics = json.load(f)
    
    print(f"--- Gemini Forced Alignment ---")
    print(f"Audio: {args.audio}")
    print(f"Lyrics: {len(lyrics)} segments")
    
    aligned = align_lyrics_with_gemini(args.audio, lyrics)
    
    output_path = args.output or args.lyrics
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(aligned, f, ensure_ascii=False, indent=2)
    
    print(f"\nSaved to {output_path}")
    print(f"Total segments: {len(aligned)}")
    for i, seg in enumerate(aligned):
        words = seg.get("words", [])
        print(f"  Seg {i}: {seg['start']:.1f}-{seg['end']:.1f}s | {len(words)} words | {seg['text'][:45]}")


if __name__ == "__main__":
    main()
