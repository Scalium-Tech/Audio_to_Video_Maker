"""
NeMo Forced Aligner Module
===========================
Uses NVIDIA NeMo's CTC-based ASR model to produce precise word-level 
timestamps for Hindi lyrics via forced alignment.

How it works:
1. Converts audio to 16kHz mono WAV
2. Gets character-level log-probabilities from NeMo's Hindi CTC model
3. Tokenizes the ground-truth lyrics into the model's character set
4. Runs CTC forced alignment (Viterbi) to find optimal character-to-frame mapping
5. Groups character timestamps into word timestamps
6. Groups word timestamps into lyric line segments

Usage:
    python3.11 nemo_align.py <audio_path> <lyrics_text_file> [--output lyrics.json]
"""

import os
import sys
import json
import re
import tempfile
import subprocess
import numpy as np
from pathlib import Path


def _convert_to_wav(audio_path, output_wav=None):
    """Convert any audio to 16kHz mono WAV for NeMo."""
    audio_path = Path(audio_path)
    if output_wav is None:
        output_wav = audio_path.with_suffix(".wav")
    output_wav = Path(output_wav)
    
    print(f"  Converting {audio_path.name} → 16kHz mono WAV...", flush=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(audio_path),
        "-ar", "16000", "-ac", "1", "-f", "wav",
        str(output_wav)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {result.stderr[:200]}")
    
    return str(output_wav)


def _strip_punctuation(text):
    """Remove punctuation from text for alignment (model needs clean text)."""
    text = re.sub(r'[,!।|.?;:\-()\'\"]+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _ctc_forced_align(log_probs, targets, blank_id=0):
    """
    CTC forced alignment using dynamic programming.
    
    Args:
        log_probs: numpy array (T, C) - log probabilities per frame
        targets: list of int - target token indices
        blank_id: blank token index (usually 0)
    
    Returns:
        List of (token_idx, start_frame, end_frame) tuples
    """
    T, C = log_probs.shape
    S = len(targets)
    
    if S == 0:
        return []
    
    # Build target sequence with blanks: [blank, t0, blank, t1, blank, ...]
    # This allows optional blanks between tokens
    expanded = [blank_id]
    for t in targets:
        expanded.append(t)
        expanded.append(blank_id)
    L = len(expanded)
    
    # DP: alpha[t][s] = log-prob of best alignment up to frame t, state s
    NEG_INF = -1e30
    alpha = np.full((T, L), NEG_INF)
    
    # Initialize: can start with blank or first token
    alpha[0][0] = log_probs[0][expanded[0]]
    if L > 1:
        alpha[0][1] = log_probs[0][expanded[1]]
    
    # Fill DP table
    for t in range(1, T):
        for s in range(L):
            # Stay in same state
            score = alpha[t-1][s]
            
            # Transition from previous state
            if s > 0:
                score = max(score, alpha[t-1][s-1])
            
            # Skip blank transition (s-2) if current and s-2 are different non-blank
            if s > 1 and expanded[s] != expanded[s-2]:
                score = max(score, alpha[t-1][s-2])
            
            alpha[t][s] = score + log_probs[t][expanded[s]]
    
    # Backtrack to find best path
    # End at last or second-to-last state
    if alpha[T-1][L-1] >= alpha[T-1][L-2]:
        best_s = L - 1
    else:
        best_s = L - 2
    
    # Backtrack
    path = [best_s]
    for t in range(T-2, -1, -1):
        s = path[-1]
        candidates = [(alpha[t][s], s)]
        if s > 0:
            candidates.append((alpha[t][s-1], s-1))
        if s > 1 and expanded[s] != expanded[s-2]:
            candidates.append((alpha[t][s-2], s-2))
        best = max(candidates, key=lambda x: x[0])
        path.append(best[1])
    
    path.reverse()
    
    # Extract token alignments (skip blanks)
    alignments = []
    current_token_state = None
    start_frame = 0
    
    for t, s in enumerate(path):
        if s % 2 == 1:  # Non-blank state
            token_idx = s // 2  # Index into original targets
            if current_token_state != s:
                if current_token_state is not None and current_token_state % 2 == 1:
                    # Close previous token
                    prev_idx = current_token_state // 2
                    alignments.append((prev_idx, start_frame, t - 1))
                current_token_state = s
                start_frame = t
        else:
            if current_token_state is not None and current_token_state % 2 == 1:
                prev_idx = current_token_state // 2
                alignments.append((prev_idx, start_frame, t - 1))
                current_token_state = s
    
    # Close last token
    if current_token_state is not None and current_token_state % 2 == 1:
        prev_idx = current_token_state // 2
        alignments.append((prev_idx, start_frame, T - 1))
    
    return alignments


def align_with_nemo(audio_path, lyrics_text, output_path=None):
    """
    Main function: Align lyrics to audio using NeMo's Hindi CTC model.
    
    Args:
        audio_path: Path to audio file (MP3/WAV/M4A)
        lyrics_text: Clean lyrics string (one line per lyric line)
        output_path: Optional path to save lyrics.json
    
    Returns:
        List of segments: [{"text": str, "start": float, "end": float, "words": [...]}]
    """
    import torch
    from nemo.collections.asr.models import ASRModel
    
    audio_path = Path(audio_path)
    original_lines = [l.strip() for l in lyrics_text.strip().split("\n") if l.strip()]
    
    if not original_lines:
        print("  ERROR: No lyrics lines provided.", flush=True)
        return None
    
    print(f"  NeMo Forced Aligner: {len(original_lines)} lyrics lines", flush=True)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Step 1: Convert to WAV
        wav_path = _convert_to_wav(audio_path, tmpdir / "audio.wav")
        
        # Step 2: Load Hindi CTC model
        model_name = "stt_hi_conformer_ctc_medium"
        print(f"  Loading model: {model_name}...", flush=True)
        model = ASRModel.from_pretrained(model_name, map_location="cpu")
        model.eval()
        
        # Step 3: Get log-probabilities from model
        print("  Computing log-probabilities...", flush=True)
        
        # Prepare audio
        import soundfile as sf
        audio_data, sr = sf.read(wav_path)
        audio_duration = len(audio_data) / sr
        print(f"  Audio duration: {audio_duration:.1f}s", flush=True)
        
        # Get log-probs using model's preprocessor + encoder
        audio_tensor = torch.tensor(audio_data, dtype=torch.float32).unsqueeze(0)
        audio_len = torch.tensor([len(audio_data)], dtype=torch.int64)
        
        with torch.no_grad():
            # NeMo 2.7.0 EncDecCTCModelBPE.forward returns (log_probs, log_probs_len, greedy_preds)
            outputs = model.forward(
                input_signal=audio_tensor, input_signal_length=audio_len
            )
            if isinstance(outputs, tuple):
                log_probs = outputs[0]
                log_probs_len = outputs[1]
            else:
                log_probs = outputs
                log_probs_len = torch.tensor([outputs.shape[1]])
        
        log_probs_np = log_probs[0].cpu().numpy()  # (T, C)
        T = log_probs_np.shape[0]
        
        # Time resolution: audio_duration / T
        frame_duration = audio_duration / T
        print(f"  {T} frames, {frame_duration*1000:.1f}ms per frame", flush=True)
        
        # Step 4: Get model's vocabulary (character set)
        vocab = model.decoder.vocabulary
        # Build char-to-index map (NeMo uses blank=len(vocab) for CTC)
        char_to_idx = {c: i for i, c in enumerate(vocab)}
        blank_id = len(vocab)  # CTC blank is the last index
        
        print(f"  Vocabulary size: {len(vocab)} + blank", flush=True)
        
        # Step 5: Prepare full text for alignment
        # Join all lines, strip punctuation
        clean_lines = [_strip_punctuation(line) for line in original_lines]
        
        # Process line by line for better accuracy
        all_segments = []
        
        # First, do a rough pass to find where each line falls
        # by aligning the full text at once
        full_clean_text = " ".join(clean_lines)
        
        # Tokenize the full text
        full_tokens = []
        full_char_list = []
        for ch in full_clean_text:
            if ch == " ":
                full_tokens.append(char_to_idx.get(" ", char_to_idx.get("▁", -1)))
                full_char_list.append(" ")
            elif ch in char_to_idx:
                full_tokens.append(char_to_idx[ch])
                full_char_list.append(ch)
            # Skip chars not in vocabulary
        
        # Filter out -1 (unknown chars)
        valid = [(t, c) for t, c in zip(full_tokens, full_char_list) if t >= 0]
        full_tokens = [v[0] for v in valid]
        full_char_list = [v[1] for v in valid]
        
        if not full_tokens:
            print("  ERROR: No valid tokens found for alignment.", flush=True)
            return None
        
        print(f"  Aligning {len(full_tokens)} tokens to {T} frames...", flush=True)
        
        # Step 6: Run CTC forced alignment
        alignments = _ctc_forced_align(log_probs_np, full_tokens, blank_id)
        
        if not alignments:
            print("  ERROR: Forced alignment returned no results.", flush=True)
            return None
        
        print(f"  Alignment done: {len(alignments)} token alignments", flush=True)
        
        # Step 7: Convert character alignments to word + line timestamps
        # Build character-level timestamps
        char_times = []
        for token_idx, start_frame, end_frame in alignments:
            char = full_char_list[token_idx]
            start_time = round(start_frame * frame_duration, 3)
            end_time = round((end_frame + 1) * frame_duration, 3)
            char_times.append({"char": char, "start": start_time, "end": end_time})
        
        # Group characters into words (split on spaces)
        all_words = []
        current_word_chars = []
        word_start = None
        word_end = None
        
        for ct in char_times:
            if ct["char"] == " ":
                if current_word_chars:
                    word_text = "".join(current_word_chars)
                    all_words.append({"word": word_text, "start": word_start, "end": word_end})
                    current_word_chars = []
                    word_start = None
            else:
                current_word_chars.append(ct["char"])
                if word_start is None:
                    word_start = ct["start"]
                word_end = ct["end"]
        
        # Don't forget last word
        if current_word_chars:
            word_text = "".join(current_word_chars)
            all_words.append({"word": word_text, "start": word_start, "end": word_end})
        
        print(f"  {len(all_words)} words extracted from alignment", flush=True)
        
        # Step 8: Map words back to original lines
        segments = _map_words_to_lines(all_words, original_lines, clean_lines)
        
        # Clean double punctuation
        for seg in segments:
            seg["text"] = seg["text"].replace("!,", "!").replace(",!", "!").replace(",।", "।")
            for w in seg.get("words", []):
                w["word"] = w["word"].replace("!,", "!").replace(",!", "!").replace(",।", "।")
        
        print(f"\n  SUCCESS: {len(segments)} aligned segments", flush=True)
        for seg in segments[:3]:
            print(f"    [{seg['start']:.2f}-{seg['end']:.2f}s] {seg['text'][:45]}")
            for w in seg.get("words", [])[:4]:
                print(f"      {w['start']:.2f}-{w['end']:.2f}: \"{w['word']}\"")
        
        # Save if output path specified
        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(segments, f, ensure_ascii=False, indent=2)
            print(f"  Saved: {output_path}", flush=True)
        
        return segments


def _map_words_to_lines(all_words, original_lines, clean_lines):
    """
    Map aligned words back to original lyric lines, re-attaching punctuation.
    """
    segments = []
    word_idx = 0
    
    for orig_line, clean_line in zip(original_lines, clean_lines):
        clean_words_in_line = clean_line.split()
        n_words = len(clean_words_in_line)
        orig_words_in_line = orig_line.split()
        
        seg_words = []
        for i in range(n_words):
            if word_idx < len(all_words):
                w = all_words[word_idx].copy()
                # Re-attach punctuation from original
                if i < len(orig_words_in_line):
                    w["word"] = orig_words_in_line[i]
                w["start"] = round(w["start"], 2)
                w["end"] = round(w["end"], 2)
                seg_words.append(w)
                word_idx += 1
        
        if seg_words:
            segments.append({
                "text": orig_line,
                "start": seg_words[0]["start"],
                "end": seg_words[-1]["end"],
                "words": seg_words
            })
        else:
            # No words matched — estimate from previous segment
            prev_end = segments[-1]["end"] if segments else 0
            segments.append({
                "text": orig_line,
                "start": round(prev_end + 0.5, 2),
                "end": round(prev_end + 3.0, 2),
                "words": []
            })
    
    return segments


# --- CLI ---
if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    
    if len(sys.argv) < 3:
        print("Usage: python3.11 nemo_align.py <audio_path> <lyrics_text_file> [--output lyrics.json]")
        sys.exit(1)
    
    audio_path = sys.argv[1]
    lyrics_path = sys.argv[2]
    
    # Parse --output flag
    output_path = None
    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        if idx + 1 < len(sys.argv):
            output_path = sys.argv[idx + 1]
    
    # Default output: video/public/lyrics.json
    if output_path is None:
        output_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "video", "public", "lyrics.json"
        )
    
    # Read lyrics
    with open(lyrics_path, "r", encoding="utf-8") as f:
        raw_text = f.read()
    
    # If file has metadata (Suno AI format), extract just the lyrics
    if "--- Lyrics ---" in raw_text or "Metadata for:" in raw_text:
        try:
            from lyrics_extractor import extract_lyrics_from_text
            lyrics_text = extract_lyrics_from_text(raw_text)
            print(f"  Extracted lyrics from metadata file")
        except ImportError:
            # Manual extraction: just get Devanagari lines
            import re
            lines = raw_text.split("\n")
            lyrics_text = "\n".join(
                l.strip() for l in lines
                if l.strip() and re.search(r'[\u0900-\u097F]', l)
                and not l.strip().startswith("[") and not l.strip().startswith("(")
            )
    else:
        lyrics_text = raw_text
    
    # Add punctuation via Gemini (NeMo strips it internally, then re-attaches from original)
    print("\n--- Adding punctuation via Gemini ---")
    try:
        from lyrics_extractor import add_punctuation_with_gemini
        punctuated = add_punctuation_with_gemini(lyrics_text)
        if punctuated and punctuated != lyrics_text:
            lyrics_text = punctuated
            print("  Punctuation added successfully")
        else:
            print("  No punctuation changes (lyrics may already have punctuation)")
    except ImportError:
        print("  WARNING: lyrics_extractor not found, skipping punctuation")
    except Exception as e:
        print(f"  WARNING: Punctuation step failed: {e}, continuing without")
    
    print("=" * 60)
    print("NeMo Forced Aligner — Hindi Word-Level Alignment")
    print("=" * 60)
    print(f"Audio: {audio_path}")
    print(f"Lyrics: {lyrics_path} ({len(lyrics_text.splitlines())} lines)")
    print(f"Output: {output_path}")
    print()
    
    result = align_with_nemo(audio_path, lyrics_text, output_path)
    
    if result:
        print(f"\nDone! {len(result)} segments written to {output_path}")
    else:
        print("\nNeMo alignment failed. Falling back to Gemini...")
        from gemini_align import full_pipeline_gemini
        result = full_pipeline_gemini(audio_path, lyrics_text)
        if result:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"Gemini fallback: {len(result)} segments written to {output_path}")
        else:
            print("Both NeMo and Gemini failed.")
            sys.exit(1)
