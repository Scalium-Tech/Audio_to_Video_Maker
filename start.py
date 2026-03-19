import os
import sys
import subprocess
import time
from pathlib import Path

# Try to import wakepy for cross-platform sleep prevention
try:
    from wakepy import keep
except ImportError:
    # If not installed, we'll try to install it or just warn
    keep = None

def main():
    print("\n" + "="*60)
    print("   🎵 LyricFlow Universal Launcher")
    print("="*60 + "\n")

    # 1. Basic folder checks
    for folder in ["input_songs", "ground_truth_lyrics", "output_song", "done"]:
        Path(folder).mkdir(exist_ok=True)

    # 2. Check for MP3 files
    mp3_files = list(Path("input_songs").glob("*.mp3"))
    if not mp3_files:
        print("❌ No MP3 files found in input_songs/")
        print("   Please drop your music files there and run start.py again.")
        sys.exit(1)

    print(f"🎵 Found {len(mp3_files)} MP3 file(s) to process")

    # 3. Determine Python command
    # On Windows it's often 'python', on Mac 'python3' or 'python3.11'
    py_cmd = sys.executable

    # 4. Run the batch processor with sleep prevention
    print("🚀 Starting Batch Processor...")
    print("⚡ Sleep prevention active (Mac/Windows/Linux)")

    args = sys.argv[1:] # Pass through any CLI arguments (like --workers)
    cmd = [py_cmd, "batch_processor.py"] + args

    exit_code = 0

    if keep:
        with keep.presenting():
            try:
                result = subprocess.run(cmd)
                exit_code = result.returncode
            except KeyboardInterrupt:
                print("\n🛑 Pipeline stopped by user.")
                sys.exit(0)
    else:
        print("⚠️  'wakepy' not installed. System might sleep during long renders.")
        print("   Install it with: pip install wakepy")
        result = subprocess.run(cmd)
        exit_code = result.returncode

    if exit_code == 0:
        print("\n" + "="*60)
        print("   ✅ COMPLETED! Check output_song/ folder")
        print("="*60 + "\n")
    elif exit_code == 2:
        print("\n" + "="*60)
        print("   ⚠️  PARTIAL — some songs failed. Check output_song/ for details.")
        print("="*60 + "\n")
    else:
        print(f"\n❌ Pipeline failed with exit code {exit_code}")

    sys.exit(exit_code)

if __name__ == "__main__":
    main()
