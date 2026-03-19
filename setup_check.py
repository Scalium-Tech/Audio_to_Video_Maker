import os
import sys
import subprocess
import shutil
from pathlib import Path

def check_command(cmd, install_help):
    """Checks if a command exists in the system PATH."""
    path = shutil.which(cmd)
    if path:
        print(f"✅ {cmd} found at: {path}")
        return True
    else:
        print(f"❌ {cmd} NOT found.")
        print(f"   How to install: {install_help}")
        return False

def main():
    print("\n" + "="*50)
    print("   LyricFlow Portability & Setup Check")
    print("="*50 + "\n")

    all_pass = True

    # 1. Check Python Version
    py_version = sys.version_info
    if py_version.major == 3 and py_version.minor >= 10:
        print(f"✅ Python {py_version.major}.{py_version.minor} found (3.10+ required)")
    else:
        print(f"❌ Python 3.10+ required. You have {py_version.major}.{py_version.minor}")
        all_pass = False

    # 2. Check FFmpeg
    ffmpeg_help = "Install from ffmpeg.org or 'brew install ffmpeg' (Mac) or 'choco install ffmpeg' (Windows)"
    if not check_command("ffmpeg", ffmpeg_help):
        all_pass = False

    # 3. Check Rclone
    rclone_help = "Install from rclone.org or 'brew install rclone' (Mac) or 'choco install rclone' (Windows)"
    if not check_command("rclone", rclone_help):
        all_pass = False

    # 4. Check .env file
    if Path(".env").exists():
        print("✅ .env file found")
    else:
        print("⚠️  .env file NOT found. Created it from .env.example")
        if Path(".env.example").exists():
            shutil.copy(".env.example", ".env")
        else:
            with open(".env", "w") as f:
                f.write("GEMINI_API_KEY=your_key_here\n")
        print("   Please edit .env and add your GEMINI_API_KEY")
        all_pass = False

    # 5. Check Folders
    for folder in ["input_songs", "ground_truth_lyrics", "output_song", "done"]:
        Path(folder).mkdir(exist_ok=True)
        print(f"✅ Folder '{folder}' is ready")

    print("\n" + "="*50)
    if all_pass:
        print("   CONGRATS! Your system is ready for LyricFlow.")
        print("   Run the pipeline with: python start.py")
    else:
        print("   PRE-FLIGHT FAILED: Please fix the issues above.")
    print("="*50 + "\n")

if __name__ == "__main__":
    main()
