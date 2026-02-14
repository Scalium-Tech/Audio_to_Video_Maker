import os
import subprocess
import logging
from pathlib import Path
from audio_separator.separator import Separator

def run_ffmpeg(command):
    """Utility to run ffmpeg commands."""
    try:
        subprocess.run(command, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg error: {e.stderr.decode()}")
        return False

def isolate_vocals(input_audio_path, output_dir="separated"):
    """
    Uses BS-Roformer (Viper-2) to isolate vocals with state-of-the-art clarity.
    Includes LUFS normalization and 100Hz High-Pass filtering.
    """
    print(f"--- Processing: {input_audio_path} ---")
    
    os.makedirs(output_dir, exist_ok=True)
    input_path = Path(input_audio_path)
    
    # 1. Pre-Processing: LUFS Normalization (-14 LUFS)
    # This ensures the vocal is in the ideal range for the separation model
    normalized_input = Path(output_dir) / f"{input_path.stem}_norm.wav"
    print("Step 1: Normalizing audio to -14 LUFS...")
    norm_cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-af", "loudnorm=I=-14:LRA=11:TP=-1.0",
        str(normalized_input)
    ]
    if not run_ffmpeg(norm_cmd):
        print("Normalization failed, proceeding with raw input.")
        normalized_input = input_path

    # Create local models directory to avoid corruption in system temp folders
    model_dir = Path("models")
    model_dir.mkdir(exist_ok=True)

    # 2. Separation: BS-Roformer
    print(f"Step 2: Isolating vocals...")
    separator = Separator(output_dir=output_dir, output_format="WAV", model_file_dir=str(model_dir))
    # Load the BS-Roformer-Viper-2 model (Viperx-1297)
    # audio-separator handles the download from Hugging Face automatically
    model_name = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
    print(f"Loading model: {model_name}...")
    separator.load_model(model_name)
    
    try:
        output_files = separator.separate(str(normalized_input))
        
        vocal_raw = None
        for file in output_files:
            if "Vocals" in file:
                vocal_raw = Path(output_dir) / file
                break
        
        if not vocal_raw or not vocal_raw.exists():
            raise FileNotFoundError("Vocal separation failed.")

        # 3. Post-Processing: 100Hz High-Pass Filter
        # Removes sub-bass rumble that can confuse transcription models
        vocal_final = Path(output_dir) / f"{input_path.stem}_vocal_clean.wav"
        print("Step 3: Applying 100Hz High-Pass filter for clarity...")
        filter_cmd = [
            "ffmpeg", "-y", "-i", str(vocal_raw),
            "-af", "highpass=f=100",
            str(vocal_final)
        ]
        
        if run_ffmpeg(filter_cmd):
            print(f"--- SUCCESS: Cleaned vocals at: {vocal_final} ---")
            return str(vocal_final)
        else:
            print("Filtering failed, returning raw vocal.")
            return str(vocal_raw)
            
    except Exception as e:
        print(f"Error during separation pipeline: {e}")
        return None

if __name__ == "__main__":
    # Test stub
    pass
