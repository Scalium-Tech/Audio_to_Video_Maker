
import main
from pathlib import Path

# Configuration
AUDIO_PATH = r"d:\best_video_maker\input_songs\Swapnaatli Paaus.mp3"
VOCAL_PATH = r"d:\best_video_maker\separated\Swapnaatli Paaus_norm_(Vocals)_model_bs_roformer_ep_317_sdr_12.wav"
MODEL = "large-v2" # Using large-v2 as per standard pipeline for this song

if __name__ == "__main__":
    print(f"Running manual override for: {Path(AUDIO_PATH).name}")
    print(f"Using vocals: {Path(VOCAL_PATH).name}")
    
    main.main(
        audio_path=AUDIO_PATH,
        language="mr", 
        model_name=MODEL,
        vocal_override_path=VOCAL_PATH
    )
