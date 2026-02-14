import whisperx
import json
from pathlib import Path

def transcribe_and_align(audio_path, language="hi", device="cpu", device_index=0):
    """
    Transcribes audio and aligns word-level timestamps using WhisperX.
    """
    print(f"--- Starting transcription & alignment for: {audio_path} ---")
    
    batch_size = 16 # reduce if low on VRAM
    compute_type = "int8" # change to "float16" for GPU if supported

    # 1. Transcribe with faster-whisper (via WhisperX)
    print("Loading model...")
    model = whisperx.load_model("large-v3", device, device_index=device_index, compute_type=compute_type)

    print("Transcribing...")
    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio, batch_size=batch_size, language=language)

    # 2. Align whisper output
    print("Aligning...")
    model_a, metadata = whisperx.load_align_model(language_code=language, device=device)
    result = whisperx.align(result["segments"], model_a, metadata, audio, device, return_char_alignments=False)

    # 3. Format result
    # We want to keep it simple for the LLM to process later
    segments = []
    for segment in result["segments"]:
        segments.append({
            "text": segment["text"].strip(),
            "start": round(segment["start"], 2),
            "end": round(segment["end"], 2),
            "words": segment.get("words", [])
        })

    return segments

if __name__ == "__main__":
    # Test stub
    pass
