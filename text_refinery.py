import os
import json
import google.generativeai as genai

def refine_lyrics_with_gemini(raw_segments, api_key=None):
    """
    Uses Google Gemini 2.5 Pro to correct Hindi lyrics, transliterate to Latin script,
    and group into poetic lines using WORD-LEVEL timestamps for precision.
    """
    print(f"--- Refining lyrics using Gemini 2.5 Pro ---")
    
    # Configure API key
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        print("Error: No Gemini API key found. Set GEMINI_API_KEY environment variable or pass api_key parameter.")
        return None
    
    genai.configure(api_key=key)
    model = genai.GenerativeModel("gemini-2.5-pro")

    # Prepare WORD-LEVEL data for LLM (not segment-level)
    # This gives Gemini precise timestamps for every single word
    word_data = []
    for seg in raw_segments:
        words = seg.get("words", [])
        if words:
            for w in words:
                # WhisperX word objects have 'word', 'start', 'end'
                if "start" in w and "end" in w:
                    word_data.append({
                        "w": w.get("word", "").strip(),
                        "s": round(w["start"], 2),
                        "e": round(w["end"], 2)
                    })
        else:
            # Fallback if no word-level data available
            word_data.append({
                "w": seg["text"].strip(),
                "s": seg["start"],
                "e": seg["end"]
            })

    prompt = f"""
You are an expert Hindi lyricist. I will provide you with WORD-LEVEL timestamps from a speech-to-text transcription of a Hindi song.

Each word has a precise "s" (start) and "e" (end) timestamp in seconds.

YOUR TASK:
1. Group these words into natural, poetic lyric lines (as they'd appear in a karaoke video).
2. For each line, set "start" = the "s" value of the FIRST word in that line, and "end" = the "e" value of the LAST word in that line.
3. DO NOT invent or modify any timestamps. Use ONLY the exact timestamps from the input data.
4. Correct Hindi spelling mistakes and ensure standard Latin transliteration.
5. Output MUST be in Latin Script (phonetic transliteration), NOT Devanagari.

CRITICAL RULES:
- Use EXACT timestamps from the word data — never interpolate or guess.
- Each line should contain 6-10 words maximum (one natural lyric line).
- Return ONLY a JSON array, no other text.

Word-Level Input Data:
{json.dumps(word_data, ensure_ascii=False)}

Example Output (STRICTLY FOLLOW THIS FORMAT):
[
  {{"text": "subah ki dhoop si naya hai yeh savera", "start": 28.84, "end": 33.39}},
  {{"text": "peeche chhoot gaya woh raat ka andhera", "start": 33.40, "end": 37.95}}
]
"""

    try:
        response = model.generate_content(prompt)
        text_response = response.text.strip()
        
        # Clean markdown formatting if model includes it
        if text_response.startswith("```json"):
            text_response = text_response[7:-3].strip()
        elif text_response.startswith("```"):
            text_response = text_response[3:-3].strip()

        # Find the first '[' and last ']' to handle cases where LLM adds conversational text
        start_idx = text_response.find('[')
        end_idx = text_response.rfind(']') + 1
        if start_idx != -1 and end_idx != 0:
            json_str = text_response[start_idx:end_idx]
            refined_lyrics = json.loads(json_str)
            return refined_lyrics
        else:
            raise ValueError("No valid JSON array found in response.")
            
    except Exception as e:
        print(f"Error during Gemini refinement: {e}")
        return None

if __name__ == "__main__":
    pass
