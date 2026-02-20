import whisperx
import json
import torch
import os
import requests
import re
from pathlib import Path
from whisperx.vads.pyannote import load_vad_model, Binarize

# Fix for Torch 2.6+ security restriction. 
# WhisperX alignment models currently use classes (like omegaconf) that Torch 2.6 restricts by default.
os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
# Fix for Windows segment link error (WinError 1314) during model download
os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1"

# Custom alignment models for languages without WhisperX defaults.
CUSTOM_ALIGN_MODELS = {
    "mr": "MahmoudAshraf/mms-300m-1130-forced-aligner", 
    "pa": "MahmoudAshraf/mms-300m-1130-forced-aligner",
    "gu": "MahmoudAshraf/mms-300m-1130-forced-aligner",
}

def transcribe_and_align(audio_path, language="hi", device="cpu", device_index=0, model_name="large-v2", lyrics_text=None):
    """
    Transcribes audio and aligns word-level timestamps using WhisperX.
    Supports custom alignment models for languages without WhisperX defaults (e.g. Marathi).
    """
    print(f"--- Starting transcription & alignment for: {audio_path} ---")
    
    batch_size = 16 # reduce if low on VRAM
    compute_type = "int8" # change to "float16" for GPU if supported

    print("Loading audio...")
    audio = whisperx.load_audio(audio_path)

    # 1. Transcribe (OR Load Text)
    if lyrics_text:
        print(f"--- Lyric Anchoring Active: Using provided text ({len(lyrics_text)} chars) ---")
        # Structure the input for alignment directly
        # WhisperX expects a specific format: [{"text": "...", "start": ..., "end": ...}]
        # But for alignment, we just need the text associated with audio.
        # Actually, whisperx.align takes a 'result' dict which must have 'segments'.
        
        # We'll create a single dummy segment with the full text, 
        # allowing the aligner to break it down.
        # IMPORTANT: Replace newlines with spaces so words aren't glued together
        clean_text = lyrics_text.replace("\n", " ").replace("  ", " ")
        audio_duration = audio.shape[0] / 16000.0
        print(f"--- Audio Duration: {audio_duration:.2f} seconds ---")
        result = {"segments": [{"text": clean_text, "start": 0.0, "end": audio_duration}]}
        model_a = None # We don't need the transcription model
    else:
        # Standard Transcription with Whisper
        print(f"Loading model: {model_name}...")
        try:
            model = whisperx.load_model(model_name, device, device_index=device_index, compute_type=compute_type)
        except Exception as e:
            print(f"Error: Failed to load Whisper model. Details: {e}")
            return None

        print("Transcribing... (skipped load_audio as it's done above)")
    
    # SOLUTION: Boost volume by 10dB to help AI hear early lyrics over loud backgrounds
    # Scaling factor for 10dB is 10^(10/20) ≈ 3.16
    audio = audio * 3.16

    # NEW: Automatic Language Detection (with 1-minute voice scan & Gemini 2.5 Flash Verification)
    # ONLY Run this if we are NOT using Lyric Anchoring (because if we have text, we don't need to guess)
    if not lyrics_text and (language == "auto" or language is None):
        print("Detecting language (scanning first 60s for vocals)...")
        duration_to_scan = 60 # seconds
        audio_segment = audio[:16000 * duration_to_scan]
        
        try:
            # Step 1: Find where vocals start using VAD
            vad_model = load_vad_model(device, token=None)
            vad_result = vad_model({"waveform": torch.from_numpy(audio_segment).unsqueeze(0), "sample_rate": 16000})
            
            # Binarize scores to get segments
            binarize = Binarize(onset=0.5, offset=0.363)
            vad_segments = binarize(vad_result)
            
            first_speech_start = None
            for segment in vad_segments.get_timeline().support():
                first_speech_start = segment.start
                break
            
            if first_speech_start is not None:
                start_sample = int(first_speech_start * 16000)
                end_sample = start_sample + (30 * 16000)
                detection_audio = audio[start_sample:end_sample]
            else:
                detection_audio = audio_segment[:30 * 16000]

            # Step 2: Whisper Rough Pass Guess
            whisper_guess = model.detect_language(detection_audio)
            print(f"Whisper guess: {whisper_guess}")

            # Step 3: Gemini 2.5 Flash Verification
            print("Verifying language with Gemini 2.5 Flash...")
            # Run a tiny transcription for Gemini to read
            rough_result = model.transcribe(detection_audio, batch_size=1, language=whisper_guess)
            rough_text = " ".join([s["text"] for s in rough_result["segments"]])
            
            # Call Gemini
            api_key = os.environ.get("GEMINI_API_KEY")
            if api_key and rough_text.strip():
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
                headers = {"Content-Type": "application/json"}
                prompt = f"""Identify the most likely language of this song transcription snippet. 
                Focus on vocabulary and grammar. 
                Even if the transcription is messy, guess the primary language.
                Return ONLY the ISO 639-1 code (e.g., 'hi', 'en', 'gu', 'mr', 'pa').
                Snippet: "{rough_text}"
                Whisper thinks it's: '{whisper_guess}'
                Code:"""
                
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.0}
                }
                
                response = requests.post(url, headers=headers, json=payload)
                if response.status_code == 200:
                    resp_json = response.json()
                    gemini_code = resp_json['candidates'][0]['content']['parts'][0]['text'].strip().lower()
                    # Clean up any extra text Gemini might have returned
                    if len(gemini_code) > 2:
                        import re
                        match = re.search(r'\b(hi|en|gu|mr|pa|bn|ta|te|kn)\b', gemini_code)
                        if match:
                            gemini_code = match.group(1)
                    
                    if gemini_code in ["hi", "en", "gu", "mr", "pa"]:
                        print(f"Gemini 2.5 Flash verified language: {gemini_code}")
                        language = gemini_code
                    else:
                        print(f"Gemini suggested code {gemini_code}, but sticking to Whisper's {whisper_guess} for stability.")
                        language = whisper_guess
                else:
                    print(f"Gemini verification failed (Status: {response.status_code}). Using Whisper guess.")
                    language = whisper_guess
            else:
                language = whisper_guess

        except Exception as e:
            print(f"Intelligent detection failed: {e}. Defaulting to English ('en').")
            language = "en"
            
    # Step 2: Perform the actual transcription (if not Anchored)
    if not lyrics_text:
        print(f"Transcribing audio with model: {model_name} (Language: {language})")
        result = model.transcribe(audio, batch_size=batch_size, language=language)

    # 2. Align whisper output
    print("Aligning...")
    align_kwargs = {"language_code": language, "device": device}
    if language in CUSTOM_ALIGN_MODELS:
        align_kwargs["model_name"] = CUSTOM_ALIGN_MODELS[language]
        print(f"Using custom alignment model: {CUSTOM_ALIGN_MODELS[language]}")

    try:
        model_a, metadata = whisperx.load_align_model(**align_kwargs)
    except Exception as e:
        print(f"Error: Failed to load alignment model. Details: {e}")
        return None
        
    result = whisperx.align(result["segments"], model_a, metadata, audio, device, return_char_alignments=False)

    # 3. Format result & Calculate alignment quality
    total_words = 0
    aligned_words = 0
    segments = []
    
    for segment in result["segments"]:
        text = segment["text"].strip()
        
        # Hallucination Filter: Skip segments that are just dots or too short/noisy
        if not text or text.replace(".", "").strip() == "":
            continue

        seg_words = segment.get("words", [])
        total_words += len(text.split())
        
        # Count words that actually have timestamps
        for w in seg_words:
            if "start" in w and "end" in w:
                aligned_words += 1

        segments.append({
            "text": text,
            "start": round(segment["start"], 2),
            "end": round(segment["end"], 2),
            "words": seg_words
        })

    # Log alignment success rate
    if total_words > 0:
        success_rate = (aligned_words / total_words) * 100
        print(f"--- Alignment Success Rate: {success_rate:.2f}% ({aligned_words}/{total_words} words) ---")
        if success_rate < 50:
            print("WARNING: Low alignment success. Timestamps may be inaccurate.")

    # Apply smart splitting after alignment
    # REVERTED: User requested to stop using .txt files/anchoring logic.
    # final_segments = split_segments_by_silence(segments, min_gap=2.0)
    
    return segments

def split_segments_by_silence(segments, min_gap=2.0):
    """
    Splits a segment into multiple segments if there is a silence gap > min_gap between words.
    Crucial for fixing "blocky" lyrics in English songs where lines are repeated.
    """
    new_segments = []
    
    for seg in segments:
        words = seg.get("words", [])
        if not words:
            new_segments.append(seg)
            continue
            
        current_segment_words = []
        if words:
             last_end_time = words[0]["start"]
        
        for w in words:
            start = w.get("start")
            end = w.get("end")
            
            if start is None or end is None:
                current_segment_words.append(w)
                continue
                
            # Check for gap
            gap = start - last_end_time
            
            if gap > min_gap and current_segment_words:
                # Close current segment
                seg_start = current_segment_words[0]["start"]
                seg_end = current_segment_words[-1]["end"]
                seg_text = " ".join([word["word"] for word in current_segment_words])
                
                new_segments.append({
                    "start": seg_start,
                    "end": seg_end,
                    "text": seg_text,
                    "words": current_segment_words
                })
                current_segment_words = []
            
            current_segment_words.append(w)
            last_end_time = end
            
        # Append remaining
        if current_segment_words:
            seg_start = current_segment_words[0].get("start", seg["start"])
            seg_end = current_segment_words[-1].get("end", seg["end"])
            seg_text = " ".join([word["word"] for word in current_segment_words])
            
            new_segments.append({
                "start": seg_start,
                "end": seg_end,
                "text": seg_text,
                "words": current_segment_words
            })
            
    return new_segments

if __name__ == "__main__":
    # Test stub
    pass
