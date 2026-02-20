import os
import json

# Language configuration for multi-language support
LANGUAGE_CONFIG = {
    "hi": {"name": "Hindi", "script_note": "Hindi → Latin (Roman) phonetics"},
    "mr": {"name": "Marathi", "script_note": "Marathi → Latin (Roman) phonetics"},
    "pa": {"name": "Punjabi", "script_note": "Punjabi → Latin (Roman) phonetics"},
    "gu": {"name": "Gujarati", "script_note": "Gujarati → Latin (Roman) phonetics"},
    "en": {"name": "English", "script_note": "Ensure natural formatting and spelling"},
}

def refine_lyrics_with_gemini(raw_segments, language="hi", api_key=None):
    """
    Uses Google Gemini 2.5 Pro to correct lyrics, transliterate to Latin script,
    and group into poetic lines using WORD-LEVEL timestamps for precision.
    Supports multiple languages via language code (hi, mr, etc.).
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
1. Format each segment into a SINGLE poetic lyric block. One block should contain 2-4 lines separated by `\n`.
2. Do NOT split the input segment into multiple JSON objects. Each input segment must result in exactly ONE output JSON object.
3. The 'start' and 'end' of the output object MUST remain identical to the input segment's 'start' and 'end'.
4. Correct {language_name} spelling mistakes and ensure standard Latin transliteration ({script_note}).
5. Output MUST be in **PLAIN Latin Script** (transliteration).
6. **NO DIACRITICAL MARKS**: Use ONLY plain English letters (a-z).
7. **IMPORTANT**: Return each segment as a single block that stays on screen for the whole duration provided. Use line breaks (`\n`) to format it naturally like a paragraph in a lyric video.
8. **DO NOT REMOVE REPETITIONS**: Even if a word or phrase is repeated (e.g., "vato mithi mithi vato"), you MUST keep every occurrence. Every word the singer says is required for alignment.

CRITICAL RULES:
- Exactly ONE JSON object per input segment.
- Use `\n` for internal line breaks.
- Use `"text"` as the key for the lyric content (e.g., {{"start": 0.0, "end": 2.0, "text": "..."}}).
- Return ONLY a JSON array.

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
                    print(f"SUCCESS: Refined lyrics generated using {model_name}", flush=True)
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

GROUND TRUTH (The correct lyrics to use):
{ground_truth}

TIMING SHELL (AI interpreted segments with word-level evidence):
{json.dumps(timing_shell, indent=2, ensure_ascii=False)}

TASK:
Map the GROUND TRUTH text to the TIMING SHELL timestamps. 
1. Preserve the 'start' and 'end' of each segment. 
2. If the AI "heard" a segment that is just intro music or noises (like "पूरा" or dots), map it to an empty string "" if it doesn't match the start of your lyrics.
3. Distribute the Ground Truth lines across the AI segments where they fit best based on the timestamps.
4. If a segment is very long, feel free to use `\n` to split the ground truth text into multiple lines within that segment.
5. Ensure the output is a valid JSON array of objects with "start", "end", and "text".
6. The text MUST be the exact ground truth {language_name} text in Latin script.
7. Total accuracy is required. RETURN ONLY THE JSON ARRAY.
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
                    return json.loads(text_response[start_idx:end_idx])
            else:
                print(f"Model {model_name} failed: {response.status_code}")
        except Exception as e:
            print(f"Error with {model_name}: {e}")

    return None
