"""
Lyrics Extractor — Automatically extracts clean Devanagari lyrics from
Suno AI export txt files (or any txt file containing lyrics mixed with metadata).

Strategy:
1. Try to find a "--- Lyrics ---" section (Suno AI format)
2. Strip section markers like [Verse 1], [Chorus], [Intro], [Outro], etc.
3. Strip stage directions in parentheses like (Rhythmic harmonium opening...)
4. Keep only lines containing Devanagari Unicode characters (U+0900–U+097F)
5. Deduplicate consecutive identical lines (keep max 2 repeats for choruses)
6. Use Gemini API to add punctuation (, ! ।) without changing text
"""

import re
import os
import json


def extract_lyrics_from_text(raw_text: str) -> str:
    """
    Extracts clean Devanagari lyrics from a raw text file that may contain
    metadata, section markers, JSON, URLs, and other non-lyric content.
    
    Works with:
    - Suno AI export files (with --- Lyrics --- section)
    - Plain lyrics files (already clean)
    - Any txt file where Devanagari text needs to be isolated
    
    Returns:
        Clean string of lyrics, one line per lyric line, separated by newlines.
    """
    if not raw_text or not raw_text.strip():
        return ""
    
    lines = raw_text.split("\n")
    
    # --- Step 1: Try to extract the "--- Lyrics ---" section (Suno AI format) ---
    lyrics_section = _extract_suno_lyrics_section(lines)
    if lyrics_section:
        lines = lyrics_section
    
    # --- Step 2: Filter and clean each line ---
    clean_lines = []
    for line in lines:
        line = line.strip()
        
        # Skip empty lines
        if not line:
            continue
        
        # Skip section markers: [Verse 1], [Chorus], [Intro], [Bridge], etc.
        if re.match(r'^\[.*\]$', line):
            continue
        
        # Skip stage directions in parentheses: (Rhythmic harmonium opening...)
        if re.match(r'^\(.*\)$', line):
            continue
        
        # Skip metadata lines (key: value format with no Devanagari)
        if re.match(r'^[A-Za-z\s]+:', line) and not _has_devanagari(line):
            continue
        
        # Skip URLs
        if re.match(r'^https?://', line) or 'http' in line:
            continue
        
        # Skip JSON-like lines
        if line.startswith('{') or line.startswith('}') or line.startswith('"'):
            continue
        
        # Skip lines that are purely ASCII/numbers (metadata, not lyrics)
        if not _has_devanagari(line):
            continue
        
        # Remove any inline section markers: [Verse 1] text here → text here
        line = re.sub(r'\[.*?\]\s*', '', line).strip()
        
        # Remove inline parenthetical directions
        line = re.sub(r'\(.*?\)', '', line).strip()
        
        if line:
            clean_lines.append(line)
    
    result = "\n".join(clean_lines)
    
    return result


def _extract_suno_lyrics_section(lines: list) -> list | None:
    """
    If the file has a Suno AI '--- Lyrics ---' section, extract just that part.
    Returns None if no such section is found (file might be plain lyrics).
    """
    lyrics_start = None
    lyrics_end = None
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        
        # Find start of lyrics section
        if stripped == "--- Lyrics ---":
            lyrics_start = i + 1
            continue
        
        # Find end of lyrics section (next --- section or metadata line)
        if lyrics_start is not None and lyrics_end is None:
            if stripped.startswith("---") and stripped.endswith("---") and len(stripped) > 6:
                lyrics_end = i
                break
            # Also stop at "Cover Art URL:" or similar metadata
            if re.match(r'^(Cover Art|Raw API|Audio URL|Image URL|Generated|Metadata)', stripped):
                lyrics_end = i
                break
    
    if lyrics_start is not None:
        if lyrics_end is None:
            lyrics_end = len(lines)
        return lines[lyrics_start:lyrics_end]
    
    return None


def _has_devanagari(text: str) -> bool:
    """Check if text contains any Devanagari Unicode characters (U+0900–U+097F)."""
    return bool(re.search(r'[\u0900-\u097F]', text))





def add_punctuation_with_gemini(lyrics_text: str, api_key: str = None) -> str:
    """
    Use Gemini API to add punctuation to clean lyrics without changing
    any words, order, or format.
    
    Adds:
    - , (comma) at natural pauses within lines
    - ! after exclamatory/devotional phrases
    - । (purna viram) at verse/sentence ends
    
    Returns:
        Punctuated lyrics text, same format (one line per lyric line).
    """
    import requests
    
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("WARNING: No GEMINI_API_KEY found. Returning lyrics without punctuation.")
        return lyrics_text
    
    if not lyrics_text or not lyrics_text.strip():
        return lyrics_text
    
    lines = lyrics_text.strip().split("\n")
    total = len(lines)
    
    # Number each line so Gemini preserves them all
    numbered = "\n".join([f"L{i+1}: {line}" for i, line in enumerate(lines)])
    
    prompt = f"""You are a Hindi/Devanagari punctuation expert. Add punctuation to these lyrics.

RULES:
- DO NOT change any words, spelling, or order
- DO NOT add or remove any lines
- DO NOT transliterate — keep everything in Devanagari
- There are exactly {total} lines. Output exactly {total} lines.
- Only ONE punctuation mark per position (never combine like !,)

PUNCTUATION TO ADD:
- , (comma) at natural pauses within a line
- ! after exclamatory/devotional phrases like जय, हर हर महादेव, ॐ नमः शिवाय, राधे राधे, गणपति बप्पा मोरया, भोलेनाथ, etc.
- । (purna viram) at the end of complete verses/sentences

INPUT LYRICS ({total} lines):
{numbered}

Return ONLY a JSON array of {total} strings, one per line, in the same order:
["line1 with punctuation", "line2 with punctuation", ...]

Return ONLY the JSON array:"""
    
    models = ["gemini-2.5-flash", "gemini-2.0-flash"]
    
    for model_name in models:
        print(f"  Adding punctuation with {model_name}...", flush=True)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192}
        }
        
        try:
            response = requests.post(url, json=payload, timeout=60)
            if response.status_code != 200:
                print(f"  {model_name}: HTTP {response.status_code}", flush=True)
                continue
            
            result = response.json()
            parts = result["candidates"][0]["content"]["parts"]
            all_texts = [p["text"] for p in parts if "text" in p]
            text_response = all_texts[-1] if all_texts else ""
            
            if not text_response:
                continue
            
            # Parse JSON array
            try:
                data = json.loads(text_response.strip())
            except json.JSONDecodeError:
                stripped = text_response.replace("```json", "").replace("```", "").strip()
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError:
                    s, e = stripped.find("["), stripped.rfind("]")
                    if s >= 0 and e > s:
                        data = json.loads(stripped[s:e+1])
                    else:
                        print(f"  {model_name}: No JSON found", flush=True)
                        continue
            
            if not isinstance(data, list):
                print(f"  {model_name}: Response is not an array", flush=True)
                continue
            
            # Validate: must have same number of lines
            if len(data) != total:
                print(f"  {model_name}: Got {len(data)} lines, expected {total}. Using best effort.", flush=True)
                if len(data) < total:
                    data.extend(lines[len(data):])
                else:
                    data = data[:total]
            
            # Clean double punctuation from each line
            cleaned = []
            for line in data:
                line = str(line)
                line = line.replace("!,", "!").replace(",!", "!")
                line = line.replace(",।", "।").replace("।,", "।")
                cleaned.append(line)
            
            result_text = "\n".join(cleaned)
            print(f"  SUCCESS: Punctuated {total} lines with {model_name}", flush=True)
            
            # Show sample
            for i, (orig, punct) in enumerate(zip(lines[:3], cleaned[:3])):
                if orig != punct:
                    print(f"    L{i+1}: \"{orig}\" -> \"{punct}\"")
            
            return result_text
            
        except json.JSONDecodeError as e:
            print(f"  {model_name}: JSON parse error: {e}", flush=True)
        except Exception as e:
            print(f"  {model_name}: Error: {e}", flush=True)
    
    print("  All models failed. Returning lyrics without punctuation.", flush=True)
    return lyrics_text


# --- CLI ---
if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv()
    
    if len(sys.argv) < 2:
        print("Usage: python lyrics_extractor.py <path_to_txt_file>")
        sys.exit(1)
    
    filepath = sys.argv[1]
    
    with open(filepath, "r", encoding="utf-8") as f:
        raw = f.read()
    
    # Step 1: Extract clean lyrics
    extracted = extract_lyrics_from_text(raw)
    
    print("=" * 50)
    print("EXTRACTED LYRICS:")
    print("=" * 50)
    print(extracted)
    print(f"Total lines: {len(extracted.splitlines())}")
    
    # Step 2: Add punctuation via Gemini
    print("\n" + "=" * 50)
    print("ADDING PUNCTUATION VIA GEMINI...")
    print("=" * 50)
    punctuated = add_punctuation_with_gemini(extracted)
    
    if punctuated != extracted:
        print("\nPUNCTUATED LYRICS:")
        print(punctuated)
        
        # Overwrite the txt file with punctuated lyrics
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(punctuated)
        print(f"\nOverwritten {filepath} with punctuated lyrics.")
    else:
        print("No changes made.")
    
    print("=" * 50)
