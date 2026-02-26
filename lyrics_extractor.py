"""
Lyrics Extractor — Automatically extracts clean Devanagari lyrics from
Suno AI export txt files (or any txt file containing lyrics mixed with metadata).

Strategy:
1. Try to find a "--- Lyrics ---" section (Suno AI format)
2. Strip section markers like [Verse 1], [Chorus], [Intro], [Outro], etc.
3. Strip stage directions in parentheses like (Rhythmic harmonium opening...)
4. Keep only lines containing Devanagari Unicode characters (U+0900–U+097F)
5. Deduplicate consecutive identical lines (keep max 2 repeats for choruses)
"""

import re


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
    
    # --- Step 3: Limit consecutive duplicates (keep max 2 for chorus repeats) ---
    deduplicated = _limit_consecutive_repeats(clean_lines, max_repeats=2)
    
    result = "\n".join(deduplicated)
    
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


def _limit_consecutive_repeats(lines: list, max_repeats: int = 2) -> list:
    """
    Limit consecutive identical lines to max_repeats.
    This handles chorus sections that repeat the same line 4+ times.
    """
    if not lines:
        return lines
    
    result = []
    count = 1
    
    for i, line in enumerate(lines):
        if i > 0 and line == lines[i - 1]:
            count += 1
        else:
            count = 1
        
        if count <= max_repeats:
            result.append(line)
    
    return result


# --- CLI test ---
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python lyrics_extractor.py <path_to_txt_file>")
        sys.exit(1)
    
    filepath = sys.argv[1]
    with open(filepath, "r", encoding="utf-8") as f:
        raw = f.read()
    
    extracted = extract_lyrics_from_text(raw)
    
    print("=" * 50)
    print("EXTRACTED LYRICS:")
    print("=" * 50)
    print(extracted)
    print("=" * 50)
    print(f"Total lines: {len(extracted.splitlines())}")
