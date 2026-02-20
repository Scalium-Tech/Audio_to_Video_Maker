import torch
import numpy as np
import whisperx
import whisperx.vad as wx_vad
import os

def test_vad():
    device = "cpu"
    # Create dummy audio (1 second of silence)
    dummy_audio = np.zeros(16000, dtype=np.float32)
    
    print("Loading VAD model...")
    try:
        vad_model = wx_vad.load_vad_model(device, use_auth_token=None)
        print("VAD model loaded.")
        
        waveform = torch.from_numpy(dummy_audio).unsqueeze(0)
        print(f"Waveform shape: {waveform.shape}")
        
        # Test the call
        vad_result = vad_model({"waveform": waveform, "sample_rate": 16000})
        print("VAD call successful.")
        print(vad_result)
    except Exception as e:
        print(f"VAD test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_vad()
