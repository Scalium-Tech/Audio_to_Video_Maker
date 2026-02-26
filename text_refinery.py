import os
import json

# Language configuration for multi-language support
LANGUAGE_CONFIG = {
    "hi": {"name": "Hindi", "script_note": "Output MUST be in Devanagari (हिन्दी) script"},
    "mr": {"name": "Marathi", "script_note": "Output MUST be in Devanagari (मराठी) script"},
    "pa": {"name": "Punjabi", "script_note": "Output MUST be in Gurmukhi (ਪੰਜਾਬੀ) script"},
    "gu": {"name": "Gujarati", "script_note": "Output MUST be in Gujarati (ગુજરાતી) script"},
    "en": {"name": "English", "script_note": "Ensure natural formatting and spelling"},
}


def _split_segments_at_newlines(lyrics):
    """
    Splits any segment that contains newlines into separate segments (one per line).
    Uses word timestamps to determine the start/end of each sub-segment.
    This ensures repeated bhajan verses appear as separate lyric blocks.
    """
    result = []
    for seg in lyrics:
        text = seg.get("text", "")
        words = seg.get("words", [])
        
        lines = text.split("\n")
        lines = [l.strip() for l in lines if l.strip()]
        
        # If only 1 line or no words, keep as-is
        if len(lines) <= 1 or not words:
            result.append(seg)
            continue
        
        # Count words per line based on whitespace splitting
        line_word_counts = []
        for line in lines:
            line_words = line.replace(",", " ").replace("।", " ").split()
            line_word_counts.append(len(line_words))
        
        total_mapped = sum(line_word_counts)
        total_actual = len(words)
        
        # Distribute words across lines proportionally
        word_idx = 0
        for line_idx, line in enumerate(lines):
            if line_idx == len(lines) - 1:
                count = total_actual - word_idx
            else:
                ratio = line_word_counts[line_idx] / max(total_mapped, 1)
                count = max(1, round(ratio * total_actual))
                count = min(count, total_actual - word_idx)
            
            line_words = words[word_idx:word_idx + count]
            word_idx += count
            
            if line_words:
                sub_seg = {
                    "text": line,
                    "start": round(line_words[0]["start"], 2),
                    "end": round(line_words[-1]["end"], 2),
                    "words": line_words
                }
            else:
                seg_duration = seg["end"] - seg["start"]
                line_duration = seg_duration / len(lines)
                sub_seg = {
                    "text": line,
                    "start": round(seg["start"] + line_idx * line_duration, 2),
                    "end": round(seg["start"] + (line_idx + 1) * line_duration, 2),
                }
            result.append(sub_seg)
    
    print(f"  [SPLIT] {len(lyrics)} segments -> {len(result)} segments (split at newlines)", flush=True)
    return result


def _reattach_word_timestamps(refined_lyrics, raw_segments):
    """
    Re-attaches word-level timestamps from the original WhisperX segments
    onto the Gemini-refined line-level lyrics by matching time ranges.
    
    For each refined line, finds all original words whose timestamps fall
    within or overlap that line's [start, end] window.
    """
    # Collect ALL words from raw segments into a flat list
    all_words = []
    for seg in raw_segments:
        for w in seg.get("words", []):
            if "start" in w and "end" in w and "word" in w:
                all_words.append({
                    "word": w["word"],
                    "start": round(w["start"], 2),
                    "end": round(w["end"], 2),
                })
    
    if not all_words:
        return refined_lyrics  # No word data available, return as-is
    
    # Sort words by start time
    all_words.sort(key=lambda w: w["start"])
    
    for line in refined_lyrics:
        line_start = line.get("start", 0)
        line_end = line.get("end", 0)
        
        # Find words that overlap with this line's time range
        # A word overlaps if: word.start < line_end AND word.end > line_start
        matched_words = [
            w for w in all_words
            if w["start"] < line_end and w["end"] > line_start
        ]
        
        if matched_words:
            line["words"] = matched_words
    
    # Sanitize word timestamps to fix unreasonable durations
    return _sanitize_word_timestamps(refined_lyrics)


def _sanitize_word_timestamps(lyrics):
    """
    Fixes unreasonable word timestamps:
    1. Caps max word duration at 3 seconds
    2. If words are bunched at the end of a segment (long initial gap),
       redistributes them evenly across the segment.
    3. Ensures words are sorted by start time within each segment.
    """
    MAX_WORD_DURATION = 3.0  # seconds
    GAP_THRESHOLD_RATIO = 0.5  # if first word takes > 50% of segment, redistribute
    
    for line in lyrics:
        words = line.get("words")
        if not words or len(words) == 0:
            continue
        
        line_start = line.get("start", 0)
        line_end = line.get("end", 0)
        segment_duration = line_end - line_start
        
        if segment_duration <= 0:
            continue
        
        # Sort words by start time
        words.sort(key=lambda w: w["start"])
        
        # Check if the first word has an unreasonably long duration
        first_word = words[0]
        first_word_duration = first_word["end"] - first_word["start"]
        
        if first_word_duration > MAX_WORD_DURATION and len(words) > 1:
            # The first word is too long - likely a misalignment.
            # Check if words are bunched at the end
            second_word_start = words[1]["start"]
            gap_before_second = second_word_start - line_start
            
            if gap_before_second > segment_duration * GAP_THRESHOLD_RATIO:
                # Words are bunched at the end. Redistribute evenly.
                actual_singing_start = second_word_start - 0.5  # Give a small lead-in
                actual_singing_start = max(actual_singing_start, line_start)
                total_singing_duration = line_end - actual_singing_start
                word_slot = total_singing_duration / len(words)
                
                for i, w in enumerate(words):
                    w["start"] = round(actual_singing_start + i * word_slot, 2)
                    w["end"] = round(w["start"] + min(word_slot * 0.9, MAX_WORD_DURATION), 2)
                    w["end"] = min(w["end"], line_end)
                
                print(f"  [SANITIZE] Redistributed {len(words)} words in segment {line_start:.1f}-{line_end:.1f}s (was bunched)", flush=True)
                line["words"] = words
                continue
            else:
                # Just cap the first word's duration
                first_word["end"] = round(first_word["start"] + MAX_WORD_DURATION, 2)
                print(f"  [SANITIZE] Capped word '{first_word['word']}' from {first_word_duration:.1f}s to {MAX_WORD_DURATION}s", flush=True)
        
        # Cap any remaining words that are too long
        for w in words:
            dur = w["end"] - w["start"]
            if dur > MAX_WORD_DURATION:
                w["end"] = round(w["start"] + MAX_WORD_DURATION, 2)
        
        line["words"] = words
    
    return lyrics

def refine_lyrics_with_gemini(raw_segments, language="hi", api_key=None):
    """
    Uses Google Gemini to correct lyrics in native script (Devanagari for Hindi/Marathi),
    add punctuation for readability, and group into max 2 poetic lines per segment.
    """
    lang_cfg = LANGUAGE_CONFIG.get(language, {"name": language.upper(), "script_note": f"{language} → Latin (Roman) phonetics"})
    language_name = lang_cfg["name"]
    script_note = lang_cfg["script_note"]

    print(f"--- Refining {language_name} lyrics using Gemini 1.5 Pro-002 ---", flush=True)
    
    # Configure API key
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        print("Error: No Gemini API key found. Set GEMINI_API_KEY environment variable or pass api_key parameter.", flush=True)
        return None
    
    # Define fallback models in order of preference
    # Using REST API to avoid gRPC/SDK crashes in this environment
    import requests
    
    models_to_try = [
        "gemini-2.5-flash",
        "gemini-2.0-flash-exp", # If available
        "gemini-1.5-pro-002",
        "gemini-1.5-flash",
        "gemini-pro"
    ]

    segment_data = []
    for seg in raw_segments:
        # Detect the ACTUAL start and end of singing within this segment
        # ignore leading/trailing instrumental silence
        words = seg.get("words", [])
        active_starts = [w["start"] for w in words if "start" in w]
        active_ends = [w["end"] for w in words if "end" in w]
        
        if active_starts and active_ends:
            # Revert to tighter timings (removed lead-in and hang-time to fix "fast" feeling)
            actual_start = min(active_starts)
            actual_end = max(active_ends)
        else:
            actual_start = seg["start"]
            actual_end = seg["end"]

        segment_data.append({
            "text": seg["text"].strip(),
            "start": round(actual_start, 2),
            "end": round(actual_end, 2)
        })

    prompt = f"""
You are an expert {language_name} lyricist. I will provide you with the EXACT timestamps when the singer is singing during each segment of a {language_name} song.

YOUR TASK:
1. Format each segment into a SINGLE poetic lyric block with **MAXIMUM 2 LINES** separated by `\n`.
2. Do NOT split the input segment into multiple JSON objects. Each input segment must result in exactly ONE output JSON object.
3. The 'start' and 'end' of the output object MUST remain identical to the input segment's 'start' and 'end'.
4. Correct {language_name} spelling mistakes. {script_note}.
5. **NATIVE SCRIPT ONLY**: Output MUST be in the native script of the language (e.g., Devanagari for Hindi/Marathi). Do NOT transliterate to Latin/Roman script.
6. **READABILITY**: Add commas (,) and punctuation marks where natural pauses occur. Use "।" (purna viram) at the end of sentences for Hindi/Marathi. This makes the lyrics more readable on screen.
7. **MAX 2 LINES**: Each segment MUST have at most 2 lines. If the text is short enough, keep it as 1 line. Never exceed 2 lines.
8. **DO NOT REMOVE REPETITIONS**: Even if a word or phrase is repeated, you MUST keep every occurrence. Every word the singer says is required for alignment.

CRITICAL RULES:
- Exactly ONE JSON object per input segment.
- Use `\n` for internal line breaks (max 1 line break = max 2 lines).
- Use `"text"` as the key for the lyric content (e.g., {{"start": 0.0, "end": 2.0, "text": "..."}}).
- Return ONLY a JSON array.
- Output in NATIVE SCRIPT, not Latin.

Segment-Level Input Data:
{json.dumps(segment_data, ensure_ascii=False)}
"""

    refined_lyrics = None
    
    for model_name in models_to_try:
        print(f"Attempting refinement with model: {model_name}...", flush=True)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
        headers = {"Content-Type": "application/json"}
        data = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"}
        }
        
        try:
            response = requests.post(url, headers=headers, json=data)
            
            if response.status_code != 200:
                print(f"Model {model_name} failed with status {response.status_code}: {response.text[:100]}...", flush=True)
                continue # Try next model
                
            # Parse response
            result_json = response.json()
            candidates = result_json.get('candidates', [])
            if not candidates:
                 try:
                    # Check for prompt feedback block
                    feedback = result_json.get('promptFeedback', {})
                    if feedback:
                        print(f"Safety Block on {model_name}: {feedback}", flush=True)
                    else:
                        print(f"No candidates returned from {model_name}.", flush=True)
                 except: 
                     pass
                 continue

            text_response = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '')
            
            if not text_response:
                continue

            text_response = text_response.strip()
            # Clean markdown
            if text_response.startswith("```json"):
                text_response = text_response[7:-3].strip()
            elif text_response.startswith("```"):
                text_response = text_response[3:-3].strip()

            # Parse JSON
            try:
                # Find brackets
                start_idx = text_response.find('[')
                end_idx = text_response.rfind(']') + 1
                if start_idx != -1 and end_idx != 0:
                    json_str = text_response[start_idx:end_idx]
                    refined_lyrics = json.loads(json_str)
                    # Re-attach word-level timestamps from the original segments
                    refined_lyrics = _reattach_word_timestamps(refined_lyrics, raw_segments)
                    refined_lyrics = _split_segments_at_newlines(refined_lyrics)
                    print(f"SUCCESS: Refined lyrics generated using {model_name} (with word timestamps)", flush=True)
                    return refined_lyrics
                else:
                    print(f"Failed to parse JSON from {model_name}", flush=True)
            except json.JSONDecodeError:
                 print(f"JSON Decode Error from {model_name}", flush=True)
                 
        except Exception as e:
            print(f"Error calling {model_name}: {e}", flush=True)
            
    print("ALL MODELS FAILED. Returning None (fallback to raw).", flush=True)
    return None


def inject_lyrics_with_gemini(timing_shell, ground_truth, language="hi", api_key=None):
    """
    Step 7 of the Injection Protocol: Maps ground truth lyrics to the AI timing shell.
    """
    lang_cfg = LANGUAGE_CONFIG.get(language, {"name": language.upper(), "script_note": f"{language} → Latin (Roman) phonetics"})
    language_name = lang_cfg["name"]

    print(f"--- Injecting Ground Truth ({language_name}) into Timing Shell ---", flush=True)
    
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        return None

    import requests

    prompt = f"""
You are a professional lyric video editor. 
I have a 'Timing Shell' (AI-heard segments with timestamps) and 'Ground Truth Lyrics' (the correct text).

GROUND TRUTH (USE THESE EXACT WORDS — DO NOT CHANGE ANYTHING):
{ground_truth}

TIMING SHELL (AI interpreted segments with word-level evidence):
{json.dumps(timing_shell, indent=2, ensure_ascii=False)}

TASK:
Map the GROUND TRUTH text to the TIMING SHELL timestamps.

CRITICAL RULES — YOU MUST FOLLOW ALL OF THESE:
1. **USE EXACT WORDS**: Copy the Ground Truth text EXACTLY as given. Do NOT correct spelling, do NOT change any words, do NOT use synonyms, do NOT add or remove any words.
2. Preserve the 'start' and 'end' of each timing shell segment.
3. If a timing segment is just intro music or noise with no matching lyrics, set its text to "".
4. Distribute the Ground Truth lines across the timing segments based on where the singing occurs.
5. **MAX 2 LINES per segment**: Use `\\n` to split lines, but never more than 1 line break per segment.
6. You may ONLY add punctuation marks (commas, । purna viram) — you must NOT change the actual words.
7. Output a valid JSON array of objects with "start", "end", and "text" keys.
8. RETURN ONLY THE JSON ARRAY, nothing else.

EXAMPLE OF WHAT IS FORBIDDEN:
- Ground truth says "सोहे" → You MUST use "सोहे", NOT "शोभे" or any other form
- Ground truth says "गजानन" → You MUST use "गजानन", NOT "गजनन" or any variation
- Do NOT transliterate to Latin/Roman script
"""

    models_to_try = ["gemini-2.5-flash", "gemini-1.5-pro-002", "gemini-1.5-flash"]
    
    for model_name in models_to_try:
        print(f"Attempting injection with model: {model_name}...", flush=True)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
        headers = {"Content-Type": "application/json"}
        data = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"}
        }
        
        try:
            response = requests.post(url, headers=headers, json=data)
            if response.status_code == 200:
                result_json = response.json()
                text_response = result_json['candidates'][0]['content']['parts'][0]['text'].strip()
                
                # Clean markdown
                if text_response.startswith("```json"):
                    text_response = text_response[7:-3].strip()
                elif text_response.startswith("```"):
                    text_response = text_response[3:-3].strip()

                start_idx = text_response.find('[')
                end_idx = text_response.rfind(']') + 1
                if start_idx != -1 and end_idx != 0:
                    injected_lyrics = json.loads(text_response[start_idx:end_idx])
                    # Re-attach word-level timestamps from the timing shell
                    injected_lyrics = _reattach_word_timestamps(injected_lyrics, timing_shell)
                    injected_lyrics = _split_segments_at_newlines(injected_lyrics)
                    return injected_lyrics
            else:
                print(f"Model {model_name} failed: {response.status_code}")
        except Exception as e:
            print(f"Error with {model_name}: {e}")

    return None
