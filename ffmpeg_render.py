"""
FFmpeg Direct Renderer — Fast Lyric Video Generation (Optimized)
================================================================
Renders lyric videos using Pillow (text) + FFmpeg (video).

Optimizations for parallel workers:
  - Internal rendering at 960×540 (4x less memory per frame)
  - FFmpeg upscales to 1920×1080 with Lanczos filter
  - Pre-allocated numpy frame buffer (no .copy() per frame)
  - VideoToolbox hardware encoder (with libx264 fallback)
  - Reusable overlay images (no Image.new() per frame)

Features:
  - Word-by-word karaoke highlighting
  - Floating sparkle particles (looping)
  - Breathing center glow
  - Progress bar
  - Perfect Devanagari/Hindi rendering via Pillow
"""

import json
import math
import os
import random
import subprocess
import shutil
import numpy as np
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter


# ─────────────────────────────────────────────────
# Font Setup
# ─────────────────────────────────────────────────

def _find_devanagari_font(size=64):
    """Find a good Devanagari font on the system."""
    font_candidates = [
        "/System/Library/Fonts/Supplemental/Kohinoor Devanagari.ttc",
        "/Library/Fonts/NotoSansDevanagari-Bold.ttf",
        "/Library/Fonts/Baloo2-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    ]
    for path in font_candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    for name in ["Kohinoor Devanagari", "Noto Sans Devanagari"]:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    try:
        result = subprocess.run(
            ["fc-match", "--format=%{file}", ":lang=hi:style=Bold"],
            capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            return ImageFont.truetype(result.stdout.strip(), size)
    except Exception:
        pass
    return ImageFont.load_default()


# ─────────────────────────────────────────────────
# Constants — Internal (half-res) vs Output (full-res)
# ─────────────────────────────────────────────────

# Output resolution (final video)
OUTPUT_W, OUTPUT_H = 1920, 1080

# Internal rendering resolution (half-res for memory savings)
RENDER_W, RENDER_H = 960, 540

FPS = 30

COLOR_NORMAL = (255, 255, 255, 255)
COLOR_ACTIVE = (255, 70, 70, 255)
COLOR_SUNG = (255, 190, 90, 255)
COLOR_SHADOW = (0, 0, 0, 220)
COLOR_GLOW = (255, 100, 100, 120)
PROGRESS_BG = (255, 255, 255, 40)
PROGRESS_FG = (255, 100, 80, 200)


# ─────────────────────────────────────────────────
# Looping Light Effects
# ─────────────────────────────────────────────────

class SparkleParticle:
    """A single floating sparkle."""
    def __init__(self):
        self.reset()
    
    def reset(self):
        self.x = random.randint(0, RENDER_W)
        self.y = random.randint(0, RENDER_H)
        self.size = random.uniform(1.0, 2.5)  # Scaled down for half-res
        self.speed_x = random.uniform(-0.15, 0.15)
        self.speed_y = random.uniform(-0.4, -0.1)  # Float upward (half speed)
        self.phase = random.uniform(0, math.pi * 2)
        self.twinkle_speed = random.uniform(2.0, 5.0)
        self.max_alpha = random.randint(100, 220)
        self.color_base = random.choice([
            (255, 220, 130),  # Gold
            (255, 255, 240),  # Warm white
            (255, 180, 100),  # Amber
            (200, 220, 255),  # Cool white
            (255, 150, 80),   # Orange
        ])
    
    def update(self, dt):
        self.x += self.speed_x
        self.y += self.speed_y
        self.phase += self.twinkle_speed * dt
        
        if self.y < -10:
            self.y = RENDER_H + 10
            self.x = random.randint(0, RENDER_W)
        if self.x < -10:
            self.x = RENDER_W + 10
        elif self.x > RENDER_W + 10:
            self.x = -10
    
    def get_alpha(self):
        """Twinkle: oscillate alpha."""
        return int(self.max_alpha * (0.3 + 0.7 * abs(math.sin(self.phase))))


def _create_particles(count=80):
    """Create a set of sparkle particles."""
    return [SparkleParticle() for _ in range(count)]


def _draw_particles(draw, particles, dt):
    """Draw and update all particles."""
    for p in particles:
        p.update(dt)
        alpha = p.get_alpha()
        r, g, b = p.color_base
        color = (r, g, b, alpha)
        
        size = p.size
        x, y = int(p.x), int(p.y)
        
        # Core bright dot
        draw.ellipse([x - size, y - size, x + size, y + size], fill=color)
        
        # Tiny cross for sparkle effect
        if size > 1.5:
            arm = int(size * 1.5)
            line_color = (r, g, b, alpha // 2)
            draw.line([(x - arm, y), (x + arm, y)], fill=line_color, width=1)
            draw.line([(x, y - arm), (x, y + arm)], fill=line_color, width=1)



def _draw_breathing_glow(draw, time_sec):
    """Subtle center breathing glow that pulses."""
    pulse = 0.3 + 0.7 * abs(math.sin(time_sec * 0.8))
    alpha = int(20 * pulse)
    
    cx, cy = RENDER_W // 2, RENDER_H // 2
    for r in range(200, 25, -10):  # Half-res radii
        fade = 1.0 - (r / 200)
        a = int(alpha * fade * fade)
        if a < 1:
            continue
        color = (255, 200, 120, a)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)


# ─────────────────────────────────────────────────
# Text Rendering (at internal resolution)
# ─────────────────────────────────────────────────

def _render_line(words, current_time, font, line_position, total_lines):
    """Render a single line with word-level coloring at internal resolution."""
    img = Image.new('RGBA', (RENDER_W, RENDER_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    colored_words = []
    for i, w in enumerate(words):
        if current_time >= w["end"]:
            color = COLOR_SUNG
        elif current_time >= w["start"]:
            color = COLOR_ACTIVE
        else:
            color = COLOR_NORMAL
        colored_words.append((w["word"], color))
    
    full_text = " ".join(w[0] for w in colored_words)
    bbox = draw.textbbox((0, 0), full_text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    line_spacing = text_height + 15  # Half-res spacing
    base_y = RENDER_H - 70 - (total_lines * line_spacing)
    y = base_y + (line_position * line_spacing)
    x = (RENDER_W - text_width) // 2
    
    # Shadow
    draw.text((x + 1, y + 1), full_text, font=font, fill=COLOR_SHADOW)
    
    # Word by word
    current_x = x
    for word, color in colored_words:
        draw.text((current_x, y), word, font=font, fill=color)
        word_bbox = draw.textbbox((0, 0), word + " ", font=font)
        current_x += word_bbox[2] - word_bbox[0]
    
    return img


def _draw_progress_bar(draw, progress):
    """Slim progress bar at bottom."""
    bar_h = 2  # Half-res
    bar_w = RENDER_W - 40
    x, y = 20, RENDER_H - 10
    draw.rounded_rectangle([x, y, x + bar_w, y + bar_h], radius=1, fill=PROGRESS_BG)
    fw = int(bar_w * progress)
    if fw > 0:
        draw.rounded_rectangle([x, y, x + fw, y + bar_h], radius=1, fill=PROGRESS_FG)


# ─────────────────────────────────────────────────
# VideoToolbox Detection
# ─────────────────────────────────────────────────

def _has_videotoolbox():
    """Check if h264_videotoolbox encoder is available."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5
        )
        return "h264_videotoolbox" in result.stdout
    except Exception:
        return False


# ─────────────────────────────────────────────────
# Main Renderer (Optimized)
# ─────────────────────────────────────────────────

def render_with_ffmpeg(audio_path, lyrics_path, background_path, output_path):
    """Render lyric video: Pillow frames (960×540) → FFmpeg pipe → MP4 (1920×1080)"""
    audio_path = Path(audio_path)
    lyrics_path = Path(lyrics_path)
    background_path = Path(background_path)
    output_path = Path(output_path)
    
    print(f"  FFmpeg Renderer: {audio_path.stem}")
    
    with open(lyrics_path, 'r', encoding='utf-8') as f:
        lyrics = json.load(f)
    
    if not lyrics:
        print("  ERROR: Empty lyrics.json")
        return False
    
    # Audio duration
    try:
        probe = subprocess.check_output([
            "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)
        ]).decode().strip()
        duration = float(probe)
    except Exception:
        duration = 300
    
    print(f"  Audio: {duration:.1f}s")
    
    # Font at half-res (32pt instead of 64pt)
    font = _find_devanagari_font(32)
    print(f"  Font: {Path(getattr(font, 'path', 'default')).name}")
    
    # Load background at internal resolution (half-res) + 40% dark overlay
    bg_raw = Image.open(str(background_path)).convert('RGBA')
    bg_raw = bg_raw.resize((RENDER_W, RENDER_H), Image.LANCZOS)
    dark_overlay = Image.new('RGBA', (RENDER_W, RENDER_H), (0, 0, 0, 102))
    bg_raw = Image.alpha_composite(bg_raw, dark_overlay)
    print(f"  Background: {background_path.name} (40% dark overlay)")
    print(f"  Internal res: {RENDER_W}×{RENDER_H} → upscaled to {OUTPUT_W}×{OUTPUT_H}")
    
    # Save background image to output folder (full-res for reference)
    bg_save_path = output_path.parent / "background.jpg"
    bg_full = Image.open(str(background_path)).convert('RGB').resize((OUTPUT_W, OUTPUT_H), Image.LANCZOS)
    bg_full.save(str(bg_save_path), quality=95)
    del bg_full  # Free memory
    print(f"  Saved background to: {bg_save_path.name}")
    
    # Pre-allocate frame buffer as numpy array (OPTIMIZATION: no .copy() per frame)
    bg_np = np.array(bg_raw)
    frame_buffer = np.empty_like(bg_np)
    
    # Pre-allocate reusable overlay image for FX
    fx_overlay = Image.new('RGBA', (RENDER_W, RENDER_H), (0, 0, 0, 0))
    fx_blank = np.zeros((RENDER_H, RENDER_W, 4), dtype=np.uint8)
    
    # Init particles
    particles = _create_particles(80)
    
    total_frames = int(duration * FPS)
    dt = 1.0 / FPS
    print(f"  Effects: sparkles, breathing glow")
    print(f"  Rendering {total_frames} frames...")
    
    # Timeline
    events = []
    for i, line in enumerate(lyrics):
        if not line.get("words") or not line.get("text", "").strip():
            continue
        appear = max(0, int((line["start"] - 0.2) * FPS))
        disappear = min(total_frames, int(line["end"] * FPS))
        events.append((appear, disappear, i))
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Detect encoder: prefer VideoToolbox hardware encoder
    use_vtb = _has_videotoolbox()
    encoder = "h264_videotoolbox" if use_vtb else "libx264"
    print(f"  Encoder: {encoder} ({'hardware' if use_vtb else 'software'})")
    
    def _build_ffmpeg_cmd(enc):
        """Build FFmpeg command for the given encoder."""
        cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgba",
            "-s", f"{RENDER_W}x{RENDER_H}", "-r", str(FPS),
            "-i", "pipe:0",
            "-i", str(audio_path),
        ]
        # Video filter: upscale to output resolution
        cmd += ["-vf", f"scale={OUTPUT_W}:{OUTPUT_H}:flags=lanczos"]
        
        if enc == "h264_videotoolbox":
            cmd += ["-c:v", "h264_videotoolbox", "-b:v", "5M"]
        else:
            cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "23"]
        
        cmd += [
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(duration), "-shortest",
            "-pix_fmt", "yuv420p",
            str(output_path)
        ]
        return cmd
    
    cmd = _build_ffmpeg_cmd(encoder)
    
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    
    last_pct = -1
    
    try:
        for frame_num in range(total_frames):
            current_time = frame_num / FPS
            progress = frame_num / max(total_frames - 1, 1)
            
            # 1. Reset frame buffer from pre-computed background (fast numpy copy)
            np.copyto(frame_buffer, bg_np)
            frame = Image.fromarray(frame_buffer, 'RGBA')
            
            # 2. Light effects overlay (reuse cleared buffer)
            fx_arr = fx_overlay.load()  # Access pixel data
            # Clear the overlay by resetting numpy array
            fx_img = Image.fromarray(fx_blank.copy(), 'RGBA')
            fx_draw = ImageDraw.Draw(fx_img)
            
            # Breathing center glow
            _draw_breathing_glow(fx_draw, current_time)
            
            # Floating sparkles
            _draw_particles(fx_draw, particles, dt)
            
            frame = Image.alpha_composite(frame, fx_img)
            
            # 3. Lyrics text — show only the current active line
            active_line = None
            for appear, disappear, line_idx in events:
                if appear <= frame_num <= disappear:
                    active_line = line_idx
            
            if active_line is not None:
                words = lyrics[active_line].get("words", [])
                if words:
                    text_overlay = _render_line(
                        words, current_time, font,
                        line_position=0, total_lines=1
                    )
                    frame = Image.alpha_composite(frame, text_overlay)
            
            # 4. Progress bar
            bar = Image.new('RGBA', (RENDER_W, RENDER_H), (0, 0, 0, 0))
            bar_draw = ImageDraw.Draw(bar)
            _draw_progress_bar(bar_draw, progress)
            frame = Image.alpha_composite(frame, bar)
            
            # Write frame as RGBA (FFmpeg handles conversion)
            proc.stdin.write(frame.tobytes())
            
            pct = int(progress * 100)
            if pct % 10 == 0 and pct != last_pct:
                print(f"    {pct}%")
                last_pct = pct
        
        proc.stdin.close()
        encode_timeout = max(300, int(duration * 3))
        proc.wait(timeout=encode_timeout)
        
        if proc.returncode == 0:
            size_mb = output_path.stat().st_size / (1024 * 1024)
            print(f"  ✅ SUCCESS: {output_path.name} ({size_mb:.1f} MB)")
            return True
        else:
            stderr = proc.stderr.read().decode()[-500:]
            # If VideoToolbox failed, retry with libx264
            if use_vtb and proc.returncode != 0:
                try:
                    proc.kill()
                except Exception:
                    pass
                print(f"  ⚠️  VideoToolbox failed, retrying with libx264...")
                return _retry_with_libx264(audio_path, lyrics_path, background_path, output_path)
            print(f"  ❌ FFmpeg failed: {stderr}")
            return False
    
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        # Try fallback if VideoToolbox was used
        if use_vtb:
            try:
                proc.kill()
            except Exception:
                pass
            print(f"  ⚠️  Retrying with libx264...")
            return _retry_with_libx264(audio_path, lyrics_path, background_path, output_path)
        return False


def _retry_with_libx264(audio_path, lyrics_path, background_path, output_path):
    """Fallback: re-render using software libx264 if VideoToolbox failed."""
    # Temporarily disable VideoToolbox detection
    original_fn = _has_videotoolbox
    try:
        globals()['_has_videotoolbox'] = lambda: False
        return render_with_ffmpeg(audio_path, lyrics_path, background_path, output_path)
    finally:
        globals()['_has_videotoolbox'] = original_fn


if __name__ == "__main__":
    import argparse, time
    parser = argparse.ArgumentParser(description="FFmpeg Direct Lyric Video Renderer")
    parser.add_argument("audio", help="Path to MP3")
    parser.add_argument("lyrics", help="Path to lyrics.json")
    parser.add_argument("background", help="Path to background image")
    parser.add_argument("-o", "--output", default="output_ffmpeg.mp4")
    args = parser.parse_args()
    
    t0 = time.time()
    success = render_with_ffmpeg(args.audio, args.lyrics, args.background, args.output)
    print(f"\n  ⏱️  Render time: {time.time()-t0:.1f}s")
    if not success:
        exit(1)
