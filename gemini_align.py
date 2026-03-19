"""
Gemini Forced Alignment Module
=============================
Sends audio + lyrics text to Gemini's multimodal API to get precise 
word-level timestamps. Used as fallback when NeMo alignment is unavailable.
"""

import os
import sys
import json
import re
import math
import base64
import argparse
from pathlib import Path

_PIPELINE_ROOT = str(Path(__file__).parent.parent)
if _PIPELINE_ROOT not in sys.path:
    sys.path.insert(0, _PIPELINE_ROOT)

try:
    from failure_evidence import save_error_context
    _HAS_EVIDENCE = True
except ImportError:
    _HAS_EVIDENCE = False

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

    from gemini_utils import call_gemini_api

    # Try multiple models
    models = [
        "gemini-3-flash-preview",
        "gemini-flash-latest",
    ]
    
    for model_name in models:
        print(f"  Attempting forced alignment with {model_name}...", flush=True)
        
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
        
        result = call_gemini_api(model_name, payload, api_key=api_key, pool="alignment")
        
        if result["status"] == "error":
            print(f"  {model_name} failed: {result.get('error')}", flush=True)
            continue
            
        try:
            response_json = result["data"]
            # Gemini may return multiple parts (thinking + text)
            parts = response_json["candidates"][0]["content"]["parts"]
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
    if _HAS_EVIDENCE:
        try:
            save_error_context(
                Path(audio_path).stem, "gemini_align/word_level",
                "All Gemini models failed for forced alignment",
                extra={"n_segments": len(lyrics_segments),
                       "models_tried": ["gemini-3-flash-preview", "gemini-flash-latest"]})
        except Exception:
            pass
    return lyrics_segments


def full_pipeline_gemini(audio_path, ground_truth_text, api_key=None):
    """
    FAST PATH: VAD (10s) + Gemini (1.5min) = ~2 min total.
    
    1. Pyannote VAD detects exactly when singing/speech occurs
    2. Gemini maps ground truth text to those speech regions with word timestamps
    """
    import requests, re, time
    
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("  WARNING: No GEMINI_API_KEY. Cannot use fast path.")
        return None
    
    audio_path = Path(audio_path)
    if not audio_path.exists():
        print(f"  WARNING: Audio file not found: {audio_path}")
        return None
    
    # ── Step 1: Pyannote VAD ──
    print("  Step 1: Voice Activity Detection...", flush=True)
    vad_start = time.time()
    
    try:
        import torch, whisperx
        audio_np = whisperx.load_audio(str(audio_path))
        sample_rate = 16000
        audio_duration = round(len(audio_np) / sample_rate, 2)
        print(f"  Audio duration: {audio_duration:.1f}s", flush=True)
        
        # Use pyannote VAD model for voice activity detection
        from whisperx.vads.pyannote import load_vad_model, Binarize
        
        vad_pipeline = load_vad_model("cpu")
        audio_tensor = torch.from_numpy(audio_np).unsqueeze(0)
        vad_result = vad_pipeline({"waveform": audio_tensor, "sample_rate": sample_rate})
        
        # Binarize to get clean speech segments
        binarize = Binarize(max_duration=30.0)
        speech_annotation = binarize(vad_result)
        
        speech_segments = []
        for seg in speech_annotation.get_timeline():
            speech_segments.append({"start": round(seg.start, 2), "end": round(seg.end, 2)})
        
        print(f"  VAD: {len(speech_segments)} speech regions in {time.time()-vad_start:.1f}s", flush=True)
        
    except Exception as e:
        print(f"  VAD failed: {e}. Using duration only.", flush=True)
        try:
            from mutagen.mp3 import MP3
            audio_duration = round(MP3(str(audio_path)).info.length, 2)
        except Exception:
            audio_duration = 210.0
        speech_segments = [{"start": 0.0, "end": audio_duration}]
    
    # ── Step 2: Clean ground truth ──
    clean_lines = []
    for line in ground_truth_text.strip().split("\n"):
        line = line.strip()
        if not line or re.match(r'^\[.*\]$', line) or re.match(r'^\(.*\)$', line):
            continue
        clean_lines.append(line)
    # Number each line so Gemini can't skip any
    numbered_lines = [f"L{i+1}: {line}" for i, line in enumerate(clean_lines)]
    numbered_text = "\n".join(numbered_lines)
    total_lines = len(clean_lines)
    
    vad_info = "\n".join([f"  Speech: {s['start']:.1f}s - {s['end']:.1f}s" for s in speech_segments])
    
    # ── Step 3: Chunked Gemini alignment ──
    # Gemini loses accuracy after ~60s, so we split into chunks
    CHUNK_DURATION = 60.0  # seconds per chunk
    
    audio_bytes = audio_path.read_bytes()
    ext = audio_path.suffix.lower()
    mime_type = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}.get(ext, "audio/mpeg")
    
    # Calculate number of chunks
    n_chunks = max(1, int(math.ceil(audio_duration / CHUNK_DURATION)))
    
    # Distribute lyrics across chunks based on VAD speech regions
    # Calculate how much speech time is in each chunk
    chunk_boundaries = []
    for c in range(n_chunks):
        c_start = c * CHUNK_DURATION
        c_end = min((c + 1) * CHUNK_DURATION, audio_duration)
        # Count speech time in this chunk
        speech_time = 0
        for sp in speech_segments:
            overlap_start = max(c_start, sp["start"])
            overlap_end = min(c_end, sp["end"])
            if overlap_end > overlap_start:
                speech_time += overlap_end - overlap_start
        chunk_boundaries.append({"start": c_start, "end": c_end, "speech_time": speech_time})
    
    # Distribute lines proportionally to speech time per chunk
    total_speech = sum(cb["speech_time"] for cb in chunk_boundaries)
    if total_speech <= 0:
        total_speech = audio_duration
    
    line_cursor = 0
    for cb in chunk_boundaries:
        proportion = cb["speech_time"] / total_speech
        n_lines = max(1, round(total_lines * proportion))
        cb["line_start"] = line_cursor
        cb["line_end"] = min(line_cursor + n_lines, total_lines)
        line_cursor = cb["line_end"]
    # Ensure last chunk gets remaining lines
    if chunk_boundaries:
        chunk_boundaries[-1]["line_end"] = total_lines
    
    print(f"  Step 2: Chunked Gemini alignment ({n_chunks} chunks, {total_lines} lines)...", flush=True)
    for cb in chunk_boundaries:
        n = cb["line_end"] - cb["line_start"]
        print(f"    Chunk [{cb['start']:.0f}s-{cb['end']:.0f}s]: {n} lines (speech: {cb['speech_time']:.1f}s)", flush=True)
    
    # Process each chunk
    import subprocess as sp_mod, tempfile
    all_segments = []
    
    for chunk_idx, cb in enumerate(chunk_boundaries):
        c_start = cb["start"]
        c_end = cb["end"]
        c_lines_start = cb["line_start"]
        c_lines_end = cb["line_end"]
        chunk_lines = clean_lines[c_lines_start:c_lines_end]
        
        if not chunk_lines:
            continue
        
        n_chunk_lines = len(chunk_lines)
        
        # Extract audio chunk as WAV using ffmpeg
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        
        try:
            sp_mod.run([
                "ffmpeg", "-y", "-i", str(audio_path),
                "-ss", str(c_start), "-to", str(c_end),
                "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le",
                tmp_path
            ], capture_output=True, timeout=30)
            
            chunk_bytes = Path(tmp_path).read_bytes()
            chunk_b64 = base64.b64encode(chunk_bytes).decode("utf-8")
            chunk_mime = "audio/wav"
        except Exception as e:
            print(f"    Chunk {chunk_idx}: ffmpeg extract failed: {e}. Using full audio.", flush=True)
            chunk_b64 = base64.b64encode(audio_bytes).decode("utf-8")
            chunk_mime = mime_type
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        
        # VAD regions relative to this chunk
        chunk_vad = []
        for sp in speech_segments:
            overlap_start = max(c_start, sp["start"])
            overlap_end = min(c_end, sp["end"])
            if overlap_end > overlap_start:
                # Make timestamps relative to chunk start
                chunk_vad.append({"start": round(overlap_start - c_start, 2), "end": round(overlap_end - c_start, 2)})
        
        chunk_vad_info = "\n".join([f"  Speech: {s['start']:.1f}s - {s['end']:.1f}s" for s in chunk_vad])
        chunk_dur = round(c_end - c_start, 2)
        
        # Number lines for this chunk
        chunk_numbered = "\n".join([f"L{j+1}: {line}" for j, line in enumerate(chunk_lines)])
        
        prompt = f"""I have an audio clip ({chunk_dur:.1f}s) and {n_chunk_lines} lines of lyrics. Your job:
1. Listen carefully and add TIMESTAMPS for EVERY line (when it is sung)
2. Add PUNCTUATION (, ! \u0964) to the words

AUDIO CLIP DURATION: {chunk_dur:.1f} seconds

SPEECH REGIONS in this clip:
{chunk_vad_info}

LYRICS \u2014 exactly {n_chunk_lines} lines. Output a segment for EVERY line:
{chunk_numbered}

OUTPUT FORMAT \u2014 JSON array with exactly {n_chunk_lines} segments:
{{"text": "line with punctuation", "start": X.XX, "end": X.XX, "words": [{{"word": "word,", "start": X.XX, "end": X.XX}}]}}

RULES:
- Output ALL {n_chunk_lines} lines. Do NOT skip any.
- Each segment 2-7 seconds. NO segment longer than 8 seconds.
- ALL timestamps between 0 and {chunk_dur:.1f}
- Word timestamps TIGHT \u2014 each word ~0.3-0.6s, no gaps between words
- Add punctuation IN words
- Return ONLY the JSON array"""
        
        from gemini_utils import call_gemini_api

        # Call Gemini
        models = ["gemini-3-flash-preview", "gemini-flash-latest"]
        chunk_data = None
        
        for model_name in models:
            print(f"    Chunk {chunk_idx} [{c_start:.0f}-{c_end:.0f}s]: Trying {model_name}...", flush=True)
            
            gen_config = {"temperature": 0.1, "maxOutputTokens": 16384}
            if "1.5" in model_name:
                # Flash 1.5 doesn't support thinkingConfig in the same way, removing it
                pass
            
            payload = {
                "contents": [{"parts": [
                    {"inlineData": {"mimeType": chunk_mime, "data": chunk_b64}},
                    {"text": prompt}
                ]}],
                "generationConfig": gen_config
            }
            
            result = call_gemini_api(model_name, payload, api_key=api_key, pool="alignment")
            
            if result["status"] == "error":
                print(f"    {model_name} failed: {result.get('error')}", flush=True)
                continue
                
            try:
                response_json = result["data"]
                parts = response_json["candidates"][0]["content"]["parts"]
                all_texts = [p["text"] for p in parts if "text" in p]
                text_response = all_texts[-1] if all_texts else ""
                
                if not text_response:
                    continue
                
                # Parse JSON
                try:
                    chunk_data = json.loads(text_response.strip())
                except json.JSONDecodeError:
                    stripped = text_response.replace("```json", "").replace("```", "").strip()
                    try:
                        chunk_data = json.loads(stripped)
                    except json.JSONDecodeError:
                        s, e = stripped.find("["), stripped.rfind("]")
                        if s >= 0 and e > s:
                            chunk_data = json.loads(stripped[s:e+1])
                        else:
                            continue
                
                if isinstance(chunk_data, list) and len(chunk_data) > 0:
                    break
                chunk_data = None
                
            except Exception as e:
                print(f"    {model_name}: Error: {e}", flush=True)
        
        if not chunk_data:
            print(f"    Chunk {chunk_idx}: All models failed. Using even distribution.", flush=True)
            # Fallback: even distribution within chunk
            if chunk_vad:
                total_vad = sum(v["end"] - v["start"] for v in chunk_vad)
                slot = total_vad / max(n_chunk_lines, 1)
                vad_cursor = 0
                vad_pos = chunk_vad[0]["start"] if chunk_vad else 0
                
                for j, line in enumerate(chunk_lines):
                    while vad_cursor < len(chunk_vad) and vad_pos >= chunk_vad[vad_cursor]["end"]:
                        vad_cursor += 1
                        if vad_cursor < len(chunk_vad):
                            vad_pos = chunk_vad[vad_cursor]["start"]
                    
                    seg_start = round(vad_pos + c_start, 2)  # absolute time
                    seg_end = round(min(vad_pos + slot, chunk_dur) + c_start, 2)
                    words = _even_words(line, seg_start, seg_end)
                    all_segments.append({"text": line, "start": seg_start, "end": seg_end, "words": words})
                    vad_pos += slot
            continue
        
        # Convert chunk-relative timestamps to absolute timestamps
        for item in chunk_data:
            text = item.get("text", "")
            # Add chunk offset to get absolute timestamps
            start = round(min(max(item.get("start", 0), 0), chunk_dur) + c_start, 2)
            end = round(min(max(item.get("end", start - c_start + 0.1), start - c_start + 0.1), chunk_dur) + c_start, 2)
            words = item.get("words", [])
            
            for w in words:
                w["start"] = round(max(start, min(w.get("start", 0) + c_start, end)), 2)
                w["end"] = round(max(w["start"] + 0.05, min(w.get("end", 0) + c_start, end)), 2)
                if w["end"] - w["start"] > 1.5:
                    w["end"] = round(w["start"] + 1.5, 2)
            
            all_segments.append({"text": text, "start": start, "end": end, "words": words})
        
        matched = sum(1 for s in chunk_data if s.get("text", "").strip())
        print(f"    Chunk {chunk_idx}: {matched}/{n_chunk_lines} lines aligned", flush=True)
    
    if not all_segments:
        print("  All chunks failed.", flush=True)
        if _HAS_EVIDENCE:
            try:
                save_error_context(
                    audio_path.stem, "gemini_align", "All Gemini alignment chunks failed",
                    extra={"n_chunks": n_chunks, "total_lines": total_lines,
                           "audio_duration": audio_duration,
                           "models_tried": ["gemini-3-flash-preview", "gemini-flash-latest"]})
            except Exception:
                pass
        return None
    
    # Sort by start time and post-process
    all_segments.sort(key=lambda s: s["start"])
    all_segments = _transfer_punctuation(all_segments)
    all_segments = _validate_and_fix_segments(all_segments, speech_segments, audio_duration)
    all_segments = _smooth_word_gaps(all_segments)
    
    non_empty = [s for s in all_segments if s["text"].strip()]
    max_ts = max(s["end"] for s in all_segments) if all_segments else 0
    print(f"  SUCCESS: {len(all_segments)} segments ({len(non_empty)} with lyrics)", flush=True)
    print(f"  Max timestamp: {max_ts:.1f}s (audio: {audio_duration:.1f}s)", flush=True)
    
    for seg in all_segments[:3]:
        if seg["text"]:
            print(f"    [{seg['start']:.1f}-{seg['end']:.1f}s] {seg['text'][:45]}")
            for w in seg.get("words", [])[:3]:
                print(f"      {w['start']:.2f}-{w['end']:.2f}: \"{w['word']}\"")
    
    return all_segments


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
   - IMPORTANT: Include ALL punctuation marks (commas, !, ।) as part of the word text. Copy words EXACTLY as they appear in the segment text above, including any trailing punctuation. For example, if the text says "भोलेनाथ!, शरण" then the word should be "भोलेनाथ!," not "भोलेनाथ".

Return a JSON array where each element has:
- "seg_index": segment number (0-based)
- "repetitions": how many times the line is sung (1 if not repeated)
- "words": array of {{"word": "...", "start": X.XX, "end": X.XX}} for the FIRST occurrence

Example:
[
  {{"seg_index": 0, "repetitions": 1, "words": [{{"word": "hello!", "start": 1.0, "end": 1.5}}]}},
  {{"seg_index": 1, "repetitions": 4, "words": [{{"word": "chorus,", "start": 5.0, "end": 5.5}}]}}
]

Return ONLY the JSON array:"""

    from gemini_utils import call_gemini_api

    models = ["gemini-3-flash-preview", "gemini-flash-latest"]
    
    for model_name in models:
        print(f"  Attempting merged align+split with {model_name}...", flush=True)
        
        gen_config = {"temperature": 0.1, "maxOutputTokens": 65536}
        if "2.5" in model_name: # Keep for future if needed, but current models don't use this
            pass
        
        payload = {
            "contents": [{"parts": [
                {"inlineData": {"mimeType": mime_type, "data": audio_b64}},
                {"text": prompt}
            ]}],
            "generationConfig": gen_config
        }
        
        result = call_gemini_api(model_name, payload, api_key=api_key)
        
        if result["status"] == "error":
            print(f"  {model_name} failed: {result.get('error')}", flush=True)
            continue
            
        try:
            response_json = result["data"]
            parts = response_json["candidates"][0]["content"]["parts"]
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
            # Transfer punctuation from segment text to words
            expanded = _transfer_punctuation(expanded)
            expanded = _smooth_word_gaps(expanded)
            
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
    """Create evenly distributed word timestamps, preserving punctuation."""
    # Split but keep punctuation attached to words
    import re
    text_words = re.findall(r'\S+', text)
    text_words = [w.strip() for w in text_words if w.strip()]
    n = max(len(text_words), 1)
    dur = end - start
    slot = dur / n
    return [{"word": tw, "start": round(start + j * slot, 2),
             "end": round(start + (j + 1) * slot - 0.03, 2)}
            for j, tw in enumerate(text_words)]


def _transfer_punctuation(segments):
    """
    Transfer punctuation from segment 'text' to individual 'words'.
    
    Problem: Gemini returns words like ['भोलेनाथ', 'शरण'] 
    but text has 'भोलेनाथ!, शरण तिहारी आए हैं।'
    
    Solution: Match each word to the text and copy trailing punctuation.
    """
    for seg in segments:
        text = seg.get("text", "")
        words = seg.get("words", [])
        if not text or not words:
            continue
        
        # Split text into tokens preserving punctuation
        import re
        text_tokens = re.findall(r'\S+', text)
        
        # Match words to text tokens
        ti = 0  # text token index
        for w in words:
            clean_word = w["word"].rstrip(",!।.?")
            # Find matching text token
            while ti < len(text_tokens):
                clean_token = text_tokens[ti].rstrip(",!।.?")
                if clean_token == clean_word:
                    # Transfer the full token (with punctuation) to the word
                    w["word"] = text_tokens[ti]
                    ti += 1
                    break
                ti += 1
        
        # Clean double punctuation: only keep one trailing symbol per word
        for w in words:
            word = w["word"]
            # Count trailing punctuation chars
            punct_chars = ""
            while word and word[-1] in ",!।.?|":
                punct_chars = word[-1] + punct_chars
                word = word[:-1]
            if len(punct_chars) > 1:
                # Keep only the first (most important) punctuation
                w["word"] = word + punct_chars[0]
        
        # Also clean segment text
        seg["text"] = seg["text"].replace("!,", "!").replace(",!", "!").replace(",।", "।").replace("।,", "।")
    
    return segments


def _smooth_word_gaps(segments):
    """
    Fill gaps between word timestamps for smooth karaoke highlighting.
    
    Problem: Gemini leaves gaps like:
      "मैया" 57.50→57.80, "तेरी" 58.00→58.20 (0.2s gap = flicker)
    
    Fix: Extend each word's end to the next word's start:
      "मैया" 57.50→58.00, "तेरी" 58.00→58.50 (smooth flow)
    """
    for seg in segments:
        words = seg.get("words", [])
        if len(words) < 2:
            continue
        
        seg_end = seg.get("end", words[-1]["end"])
        
        for i in range(len(words) - 1):
            next_start = words[i + 1]["start"]
            gap = next_start - words[i]["end"]
            # Fill gaps up to 0.5s (larger gaps are likely real pauses)
            if 0 < gap <= 0.5:
                words[i]["end"] = round(next_start, 2)
        
        # Extend last word to segment end (if within 1.5s)
        if words:
            last_gap = seg_end - words[-1]["end"]
            if 0 < last_gap <= 1.5:
                words[-1]["end"] = round(seg_end, 2)
    
    return segments


def _validate_and_fix_segments(segments, speech_segments, audio_duration):
    """
    Detect and fix common Gemini alignment failures:
    1. Segments too wide (e.g. 43s for one line) -> trim to word range
    2. Segments crammed at end (0.05s wide) -> redistribute to unused VAD regions
    """
    import re
    
    if not segments or not speech_segments:
        return segments
    
    # -- Fix 1: Trim overly wide segments --
    for seg in segments:
        words = seg.get("words", [])
        seg_dur = seg["end"] - seg["start"]
        
        if seg_dur > 10 and words and len(words) >= 2:
            first_word_start = words[0]["start"]
            last_word_end = words[-1]["end"]
            word_span = last_word_end - first_word_start
            
            if word_span < seg_dur * 0.5:
                new_start = round(max(first_word_start - 0.3, 0), 2)
                new_end = round(min(last_word_end + 0.5, audio_duration), 2)
                print(f"  \u26a0\ufe0f  Trimmed wide segment: [{seg['start']:.1f}-{seg['end']:.1f}s] -> [{new_start:.1f}-{new_end:.1f}s] \"{seg['text'][:30]}\"", flush=True)
                seg["start"] = new_start
                seg["end"] = new_end
    
    # -- Fix 2: Detect crammed segments (near-zero duration) --
    crammed = []
    good = []
    for i, seg in enumerate(segments):
        if seg["end"] - seg["start"] < 0.5 and seg.get("text", "").strip():
            crammed.append(i)
        else:
            good.append(i)
    
    if not crammed:
        return segments
    
    print(f"  \u26a0\ufe0f  Found {len(crammed)} crammed segments \u2014 redistributing to unused speech regions", flush=True)
    
    # Find speech regions not covered by good segments
    used_ranges = [(segments[i]["start"], segments[i]["end"]) for i in good if segments[i].get("text", "").strip()]
    
    unused_speech = []
    for sp in speech_segments:
        sp_start, sp_end = sp["start"], sp["end"]
        covered = 0
        for us, ue in used_ranges:
            overlap_start = max(sp_start, us)
            overlap_end = min(sp_end, ue)
            if overlap_end > overlap_start:
                covered += overlap_end - overlap_start
        
        sp_dur = sp_end - sp_start
        if sp_dur - covered > 2.0:
            latest_end = sp_start
            for us, ue in used_ranges:
                if us >= sp_start and ue <= sp_end and ue > latest_end:
                    latest_end = ue
            
            if sp_end - latest_end > 2.0:
                unused_speech.append({"start": latest_end, "end": sp_end})
    
    if not unused_speech:
        half = audio_duration / 2
        unused_speech = [{"start": half, "end": audio_duration}]
    
    # Distribute crammed segments across unused speech regions
    total_unused = sum(u["end"] - u["start"] for u in unused_speech)
    slot_dur = min(total_unused / max(len(crammed), 1), 5.0)
    
    cursor = 0
    cursor_pos = unused_speech[0]["start"] if unused_speech else audio_duration / 2
    
    for idx in crammed:
        seg = segments[idx]
        
        while cursor < len(unused_speech) and cursor_pos >= unused_speech[cursor]["end"]:
            cursor += 1
            if cursor < len(unused_speech):
                cursor_pos = unused_speech[cursor]["start"]
        
        if cursor >= len(unused_speech):
            break
        
        new_start = round(cursor_pos, 2)
        new_end = round(min(cursor_pos + slot_dur, unused_speech[cursor]["end"]), 2)
        
        text_words = re.findall(r'\S+', seg["text"])
        n = max(len(text_words), 1)
        word_dur = (new_end - new_start) / n
        new_words = [{"word": tw, "start": round(new_start + j * word_dur, 2),
                      "end": round(new_start + (j + 1) * word_dur - 0.03, 2)}
                     for j, tw in enumerate(text_words)]
        
        print(f"  -> Redistributed: \"{seg['text'][:35]}\" -> [{new_start:.1f}-{new_end:.1f}s]", flush=True)
        seg["start"] = new_start
        seg["end"] = new_end
        seg["words"] = new_words
        
        cursor_pos = new_end + 0.3
    
    segments.sort(key=lambda s: s["start"])
    
    return segments


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

    from gemini_utils import call_gemini_api

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
    
    result = call_gemini_api("gemini-3-flash-preview", payload, api_key=api_key)
    
    if result["status"] == "error":
        print(f"  Failed: {result.get('error')}", flush=True)
        return lyrics_segments
        
    try:
        response_json = result["data"]
        
        # Collect ALL text from all parts
        parts = response_json["candidates"][0]["content"]["parts"]
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
