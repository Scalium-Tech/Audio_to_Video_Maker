"""
FFmpeg Direct Renderer — Fast Lyric Video Generation (Optimized)
================================================================
Renders lyric videos using Pillow (text) + FFmpeg (video).

Optimizations for parallel workers:
  - Internal rendering at 960×540 (4x less memory per frame)
  - FFmpeg upscales to 1920×1080 with Lanczos filter
  - Pre-allocated numpy frame buffer (no .copy() per frame)
  - HEVC (H.265) hardware encoder via VideoToolbox (~70% smaller files)
  - Fallback chain: hevc_videotoolbox → h264_videotoolbox → libx264
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
import platform
import random
import subprocess
import shutil
import numpy as np
import time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from style_variants import choose_style_variant

_LAST_RENDER_ERROR = {"stage": "", "message": ""}


def _set_render_error(stage, message):
    _LAST_RENDER_ERROR["stage"] = stage
    _LAST_RENDER_ERROR["message"] = str(message)


def get_last_render_error():
    return dict(_LAST_RENDER_ERROR)


def _clear_render_error():
    _LAST_RENDER_ERROR["stage"] = ""
    _LAST_RENDER_ERROR["message"] = ""


def _tail_text_file(path: Path, max_chars: int = 4000) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
        return text[-max_chars:]
    except Exception:
        return ""


# ─────────────────────────────────────────────────
# Font Setup
# ─────────────────────────────────────────────────

def _find_devanagari_font(size=64):
    """Find a good Devanagari font on the system (Windows/Mac/Linux)."""
    font_candidates = [
        # Mac
        "/System/Library/Fonts/Supplemental/Kohinoor Devanagari.ttc",
        "/Library/Fonts/NotoSansDevanagari-Bold.ttf",
        # Windows
        "C:\\Windows\\Fonts\\mangal.ttf",
        "C:\\Windows\\Fonts\\mangalb.ttf",
        "C:\\Windows\\Fonts\\Nirmala.ttf",
        "C:\\Windows\\Fonts\\NirmalaB.ttf",
        # Linux / Common
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
        "~/.fonts/NotoSansDevanagari-Bold.ttf",
    ]
    
    # Add common font search
    import platform
    if platform.system() == "Windows":
        windir = os.environ.get("WINDIR", "C:\\Windows")
        font_candidates.append(os.path.join(windir, "Fonts", "mangal.ttf"))
        font_candidates.append(os.path.join(windir, "Fonts", "Nirmala.ttf"))

    for path in font_candidates:
        expanded = os.path.expanduser(path)
        if os.path.exists(expanded):
            try:
                return ImageFont.truetype(expanded, size)
            except Exception:
                continue
    
    # Generic search by name
    for name in ["Kohinoor Devanagari", "Noto Sans Devanagari", "Mangal", "Nirmala UI"]:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
            
    # Fallback to fc-match on Linux
    try:
        if platform.system() != "Windows":
            result = subprocess.run(
                ["fc-match", "--format=%{file}", ":lang=hi:style=Bold"],
                capture_output=True, text=True, timeout=2
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

FPS = 24

COLOR_NORMAL = (255, 255, 255, 255)
COLOR_ACTIVE = (255, 70, 70, 255)
COLOR_SUNG = (255, 190, 90, 255)
COLOR_SHADOW = (0, 0, 0, 220)
COLOR_GLOW = (255, 100, 100, 120)
PROGRESS_BG = (255, 255, 255, 40)
PROGRESS_FG = (255, 100, 80, 200)

DEITY_THEMES = {
    "shiva": {
        "normal": (255, 255, 255, 255),
        "active": (255, 140, 0, 255),
        "sung": (255, 200, 100, 255),
        "glow": (255, 165, 0, 120),
    },
    "krishna": {
        "normal": (255, 255, 255, 255),
        "active": (100, 180, 255, 255),
        "sung": (255, 220, 80, 255),
        "glow": (100, 150, 255, 120),
    },
    "ram": {
        "normal": (255, 255, 255, 255),
        "active": (255, 100, 50, 255),
        "sung": (255, 200, 120, 255),
        "glow": (255, 120, 50, 120),
    },
    "ganesh": {
        "normal": (255, 255, 255, 255),
        "active": (220, 50, 50, 255),
        "sung": (255, 180, 80, 255),
        "glow": (255, 80, 50, 120),
    },
    "durga": {
        "normal": (255, 255, 255, 255),
        "active": (255, 50, 50, 255),
        "sung": (255, 215, 0, 255),
        "glow": (255, 50, 50, 120),
    },
    "default": {
        "normal": COLOR_NORMAL,
        "active": COLOR_ACTIVE,
        "sung": COLOR_SUNG,
        "glow": COLOR_GLOW,
    },
}


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


def _build_sparkle_sprite(size_bucket, color_base, alpha_bucket):
    """Pre-render a sparkle sprite so the frame loop can reuse it."""
    radius = max(2, int(round(size_bucket * 2)))
    dim = max(12, radius * 6)
    center = dim // 2
    img = Image.new("RGBA", (dim, dim), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r, g, b = color_base

    glow_color = (r, g, b, max(10, alpha_bucket // 4))
    core_color = (r, g, b, alpha_bucket)
    line_color = (r, g, b, max(20, alpha_bucket // 2))

    draw.ellipse(
        [center - radius * 2, center - radius * 2, center + radius * 2, center + radius * 2],
        fill=glow_color,
    )
    draw.ellipse(
        [center - radius, center - radius, center + radius, center + radius],
        fill=core_color,
    )
    arm = max(2, int(radius * 1.5))
    draw.line([(center - arm, center), (center + arm, center)], fill=line_color, width=1)
    draw.line([(center, center - arm), (center, center + arm)], fill=line_color, width=1)
    return img.filter(ImageFilter.GaussianBlur(radius=0.35))


def _draw_particles(img, particles, dt, sprite_cache):
    """Draw and update all particles using cached sparkle sprites."""
    for p in particles:
        p.update(dt)
        alpha = p.get_alpha()
        size_bucket = round(p.size * 2) / 2
        alpha_bucket = max(48, min(224, int(round(alpha / 16.0) * 16)))
        key = (size_bucket, p.color_base, alpha_bucket)
        sprite = sprite_cache.get(key)
        if sprite is None:
            sprite = _build_sparkle_sprite(size_bucket, p.color_base, alpha_bucket)
            sprite_cache[key] = sprite
        dest = (int(p.x) - sprite.width // 2, int(p.y) - sprite.height // 2)
        img.alpha_composite(sprite, dest=dest)



def _lerp_color(c1, c2, t):
    """Linear interpolation between two RGBA colors."""
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def _get_theme_for_song(song_name: str) -> dict:
    name_lower = song_name.lower()
    for deity, theme in DEITY_THEMES.items():
        if deity != "default" and deity in name_lower:
            return theme
    return DEITY_THEMES["default"]


def _draw_breathing_glow(draw, time_sec, glow_color=None):
    """Subtle center breathing glow that pulses."""
    pulse = 0.3 + 0.7 * abs(math.sin(time_sec * 0.8))
    alpha = int(20 * pulse)
    glow_color = glow_color or COLOR_GLOW
    
    cx, cy = RENDER_W // 2, RENDER_H // 2
    for r in range(200, 25, -10):  # Half-res radii
        fade = 1.0 - (r / 200)
        a = int(alpha * fade * fade)
        if a < 1:
            continue
        color = (glow_color[0], glow_color[1], glow_color[2], a)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)


def _build_glow_frame(glow_color, pulse):
    """Pre-render a single glow frame for sprite reuse."""
    img = Image.new("RGBA", (RENDER_W, RENDER_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    alpha = int(20 * pulse)
    cx, cy = RENDER_W // 2, RENDER_H // 2
    for r in range(200, 25, -10):
        fade = 1.0 - (r / 200)
        a = int(alpha * fade * fade)
        if a < 1:
            continue
        color = (glow_color[0], glow_color[1], glow_color[2], a)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    return img


def _precompute_glow_frames(glow_color, frame_count=24):
    frames = []
    for idx in range(frame_count):
        pulse = 0.3 + 0.7 * abs(math.sin((idx / frame_count) * math.pi * 2))
        frames.append(_build_glow_frame(glow_color, pulse))
    return frames


def _render_background_frame(bg_source, dark_overlay, progress):
    """
    Render a center-locked background frame with a very subtle push-in.
    This animates only the artwork, not the lyrics/effects layer.
    """
    progress = max(0.0, min(1.0, progress))
    ease = progress * progress * (3 - 2 * progress)
    zoom_strength = float(os.environ.get("LYRICFLOW_BG_ZOOM_STRENGTH", "0.02"))
    zoom = 1.0 + (zoom_strength * ease)

    src_w, src_h = bg_source.size
    crop_w = src_w / zoom
    crop_h = src_h / zoom
    left = (src_w - crop_w) / 2
    top = (src_h - crop_h) / 2
    box = (left, top, left + crop_w, top + crop_h)

    frame = bg_source.crop(box).resize((RENDER_W, RENDER_H), Image.LANCZOS)
    return Image.alpha_composite(frame, dark_overlay)


def _render_background_motion_clip(bg_source, dark_overlay, clip_path, total_frames):
    """Pre-render a background-only motion clip for smooth two-pass compositing."""
    clip_path = Path(clip_path)
    clip_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgba",
        "-s", f"{RENDER_W}x{RENDER_H}", "-r", str(FPS),
        "-i", "pipe:0",
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
        "-pix_fmt", "yuv420p",
        str(clip_path),
    ]

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        for frame_num in range(total_frames):
            progress = frame_num / max(total_frames - 1, 1)
            frame = _render_background_frame(bg_source, dark_overlay, progress)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        proc.wait(timeout=max(120, int(total_frames / FPS) * 2))
        if proc.returncode != 0:
            stderr = proc.stderr.read().decode(errors="ignore")[-1500:]
            raise RuntimeError(f"Background clip render failed: {stderr}")
        return clip_path
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass


def _render_static_background_clip(bg_source, dark_overlay, clip_path, duration, total_frames):
    """Fallback background clip with no motion when animated clip generation fails."""
    clip_path = Path(clip_path)
    still_path = clip_path.with_name("_background_still.png")
    frame = _render_background_frame(bg_source, dark_overlay, 0.0).convert("RGB")
    frame.save(still_path)
    try:
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", str(still_path),
            "-r", str(FPS),
            "-frames:v", str(total_frames),
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "14",
            "-pix_fmt", "yuv420p",
            str(clip_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=max(180, int(duration * 2)))
        if result.returncode != 0:
            stderr = (result.stderr or result.stdout or "")[-1500:]
            raise RuntimeError(f"Static background clip fallback failed: {stderr}")
        return clip_path
    finally:
        try:
            still_path.unlink()
        except Exception:
            pass


# ─────────────────────────────────────────────────
# Text Rendering (at internal resolution)
# ─────────────────────────────────────────────────

def _normalize_line_text(text):
    return " ".join((text or "").split()).strip(" .,!?:;|।")


def _detect_chorus_lines(lyrics):
    counts = {}
    for entry in lyrics:
        normalized = _normalize_line_text(entry.get("text", ""))
        if len(normalized) < 8 or len(normalized.split()) < 2:
            continue
        counts[normalized] = counts.get(normalized, 0) + 1

    chorus_texts = {text for text, count in counts.items() if count >= 2}
    indices = set()
    for idx, entry in enumerate(lyrics):
        if _normalize_line_text(entry.get("text", "")) in chorus_texts:
            indices.add(idx)
    return indices


def _get_line_palette(theme, is_chorus):
    if not is_chorus:
        return theme
    return {
        "normal": _lerp_color(theme["normal"], (255, 245, 210, 255), 0.2),
        "active": _lerp_color(theme["active"], (255, 255, 255, 255), 0.18),
        "sung": _lerp_color(theme["sung"], (255, 235, 170, 255), 0.15),
        "glow": theme["glow"],
    }


def _render_line_on_img(img, words, current_time, font, line_position, total_lines, theme=None, style_variant=None, is_chorus=False):
    """Draw a single line with word-level coloring onto an existing image."""
    draw = ImageDraw.Draw(img)
    theme = theme or DEITY_THEMES["default"]
    style_variant = style_variant or choose_style_variant("default")
    palette = _get_line_palette(theme, is_chorus)
    fade_duration = 0.2
    
    colored_words = []
    for i, w in enumerate(words):
        if current_time >= w["end"]:
            t = min(1.0, (current_time - w["end"]) / fade_duration)
            color = _lerp_color(palette["active"], palette["sung"], t)
        elif current_time >= w["start"]:
            t = min(1.0, (current_time - w["start"]) / fade_duration)
            color = _lerp_color(palette["normal"], palette["active"], t)
        else:
            color = palette["normal"]
        colored_words.append((w["word"], color))
    
    full_text = " ".join(w[0] for w in colored_words)
    bbox = draw.textbbox((0, 0), full_text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    line_spacing = text_height + 15
    # Center the block of 'total_lines' vertically
    block_height = total_lines * line_spacing
    # Keep lyrics in the lower third so the deity/background subject stays visible.
    base_y = int(RENDER_H * style_variant["line_y_ratio"]) - (block_height // 2)
    
    y = base_y + (line_position * line_spacing)
    x = (RENDER_W - text_width) // 2

    # Word by word
    current_x = x
    for word, color in colored_words:
        draw.text(
            (current_x, y),
            word,
            font=font,
            fill=color,
            stroke_width=3,
            stroke_fill=(0, 0, 0, 255),
        )
        word_bbox = draw.textbbox((0, 0), word + " ", font=font)
        current_x += word_bbox[2] - word_bbox[0]


def _draw_progress_bar(draw, progress, progress_bg=PROGRESS_BG, progress_fg=PROGRESS_FG):
    """Slim progress bar at bottom."""
    bar_h = 2  # Half-res
    bar_w = RENDER_W - 40
    x, y = 20, RENDER_H - 10
    draw.rounded_rectangle([x, y, x + bar_w, y + bar_h], radius=1, fill=PROGRESS_BG)
    fw = int(bar_w * progress)
    if fw > 0:
        draw.rounded_rectangle([x, y, x + fw, y + bar_h], radius=1, fill=PROGRESS_FG)


# ─────────────────────────────────────────────────
# Encoder Detection (HEVC preferred)
# ─────────────────────────────────────────────────

def _detect_best_encoder():
    """
    Detect the best available hardware encoder (Mac/Windows/Linux).
    Priority: 
      1. HEVC Hardware (videotoolbox, nvenc, qsv, amf)
      2. H.264 Hardware
      3. Software libx264
    """
    forced = os.environ.get("LYRICFLOW_FORCE_ENCODER", "").strip()
    if forced:
        return forced

    # On this Mac setup, VideoToolbox HEVC has been consistently failing with
    # BrokenPipeError during long renders. Default to software for reliability
    # unless explicitly re-enabled.
    if platform.system() == "Darwin" and os.environ.get("LYRICFLOW_ALLOW_VIDEOTOOLBOX", "").strip() != "1":
        return "libx264"

    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5
        )
        encoders = result.stdout
        
        # Priority 1: HEVC Hardware
        if "hevc_videotoolbox" in encoders: return "hevc_videotoolbox"
        if "hevc_nvenc" in encoders: return "hevc_nvenc"
        if "hevc_qsv" in encoders: return "hevc_qsv"
        if "hevc_amf" in encoders: return "hevc_amf"
        
        # Priority 2: H.264 Hardware
        if "h264_videotoolbox" in encoders: return "h264_videotoolbox"
        if "h264_nvenc" in encoders: return "h264_nvenc"
        if "h264_qsv" in encoders: return "h264_qsv"
        if "h264_amf" in encoders: return "h264_amf"
        
    except Exception:
        pass
    return "libx264"


# ─────────────────────────────────────────────────
# Main Renderer (Optimized)
# ─────────────────────────────────────────────────

def render_with_ffmpeg(audio_path, lyrics_path, background_path, output_path, max_duration=None):
    """Render lyric video: Pillow frames (960×540) → FFmpeg pipe → MP4 (1920×1080)
    
    Args:
        max_duration: If set, render only this many seconds (for --preview mode).
    """
    audio_path = Path(audio_path)
    lyrics_path = Path(lyrics_path)
    background_path = Path(background_path)
    output_path = Path(output_path)
    _clear_render_error()
    
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
    theme = _get_theme_for_song(audio_path.stem)
    style_variant = choose_style_variant(audio_path.stem)
    
    # Preview mode: cap duration
    if max_duration and max_duration < duration:
        duration = max_duration
        print(f"  🎬 Preview mode: rendering first {duration:.0f}s only")
    
    font = _find_devanagari_font(40)
    print(f"  Font: {Path(getattr(font, 'path', 'default')).name}")
    
    # Extra overscan gives the artwork room for a subtle push-in without moving
    # the lyric/text overlay layer.
    overscan = 1.10
    bg_source = Image.open(str(background_path)).convert('RGBA')
    bg_source = bg_source.resize(
        (int(RENDER_W * overscan), int(RENDER_H * overscan)),
        Image.LANCZOS,
    )
    dark_overlay = Image.new('RGBA', (RENDER_W, RENDER_H), (0, 0, 0, style_variant["dark_overlay_alpha"]))
    print(f"  Background: {background_path.name} (subtle artwork-only zoom + variable dark overlay)")
    print(f"  Style Variant: {style_variant['key']} ({style_variant['label']})")
    print(f"  Internal res: {RENDER_W}×{RENDER_H} → upscaled to {OUTPUT_W}×{OUTPUT_H}")
    
    # Save background image to output folder (full-res for reference)
    bg_save_path = output_path.parent / "background.jpg"
    bg_full = Image.open(str(background_path)).convert('RGB').resize((OUTPUT_W, OUTPUT_H), Image.LANCZOS)
    bg_full.save(str(bg_save_path), quality=95)
    del bg_full  # Free memory
    print(f"  Saved background to: {bg_save_path.name}")
    
    overlay_blank = np.zeros((RENDER_H, RENDER_W, 4), dtype=np.uint8)
    
    # Init particles
    particles = _create_particles(style_variant["particle_count"])
    sprite_cache = {}
    glow_frames = _precompute_glow_frames(theme["glow"])
    chorus_line_indices = _detect_chorus_lines(lyrics)
    
    total_frames = int(duration * FPS)
    dt = 1.0 / FPS
    print(f"  Effects: sparkles, breathing glow")
    print(f"  Rendering {total_frames} frames...")

    motion_clip_path = output_path.parent / "_background_motion.mp4"
    print(f"  Preparing background motion clip...")
    try:
        _render_background_motion_clip(bg_source, dark_overlay, motion_clip_path, total_frames)
    except Exception as motion_error:
        print(f"  ⚠️  Animated background failed, retrying with static background clip...")
        try:
            _render_static_background_clip(bg_source, dark_overlay, motion_clip_path, duration, total_frames)
        except Exception as static_error:
            _set_render_error("Background Motion", f"{motion_error}\nStatic fallback also failed: {static_error}")
            print(f"  ❌ Background clip failed: {_LAST_RENDER_ERROR['message']}")
            return False

    cached_text_img = None
    cached_text_state = None
    
    # Timeline: Group into fixed pairs (2 lines)
    events = []
    # Process in chunks of 2
    for i in range(0, len(lyrics), 2):
        pair = lyrics[i:i+2]
        if not pair: continue
        
        line_indices = []
        pair_start = float('inf')
        pair_end = 0
        
        for j, line in enumerate(pair):
            if not line.get("words") or not line.get("text", "").strip():
                continue
            line_indices.append(i + j)
            pair_start = min(pair_start, line["start"])
            pair_end = max(pair_end, line["end"])
            
        if line_indices:
            appear = max(0, int((pair_start - 0.2) * FPS))
            # Keep pair visible until the last line in pair ends
            disappear = min(total_frames, int(pair_end * FPS))
            events.append((appear, disappear, line_indices))
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Detect best encoder: HEVC hardware > H.264 hardware > software
    encoder = _detect_best_encoder()
    is_hardware = "videotoolbox" in encoder
    is_hevc = "hevc" in encoder
    enc_label = f"{'HEVC' if is_hevc else 'H.264'} ({'hardware' if is_hardware else 'software'})"
    print(f"  Encoder: {encoder} ({enc_label})")
    
    def _build_ffmpeg_cmd(enc):
        """Build FFmpeg command for the given encoder."""
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(motion_clip_path),
            "-f", "rawvideo", "-pix_fmt", "rgba",
            "-s", f"{RENDER_W}x{RENDER_H}", "-r", str(FPS),
            "-i", "pipe:0",
            "-i", str(audio_path),
        ]
        cmd += [
            "-filter_complex",
            f"[0:v][1:v]overlay=0:0:eof_action=repeat:shortest=0:format=auto,trim=duration={duration:.3f},setpts=PTS-STARTPTS,scale={OUTPUT_W}:{OUTPUT_H}:flags=lanczos[v]",
            "-map", "[v]",
            "-map", "2:a:0",
        ]
        
        if enc == "hevc_videotoolbox":
            # HEVC hardware: ~70% smaller than H.264 @ 5M, same quality
            cmd += ["-c:v", "hevc_videotoolbox", "-b:v", "2500k", "-tag:v", "hvc1"]
        elif enc == "h264_videotoolbox":
            cmd += ["-c:v", "h264_videotoolbox", "-b:v", "3M"]
        else:
            cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "23"]
        
        cmd += [
            "-c:a", "aac", "-b:a", "256k",
            "-r", str(FPS),
            "-frames:v", str(total_frames),
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            str(output_path)
        ]
        return cmd
    
    cmd = _build_ffmpeg_cmd(encoder)
    stderr_log_path = output_path.parent / "_ffmpeg_stderr.log"
    if stderr_log_path.exists():
        try:
            stderr_log_path.unlink()
        except Exception:
            pass
    
    # Cross-platform subprocess handling: hide console window on Windows
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    stderr_handle = open(stderr_log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=stderr_handle,
        startupinfo=startupinfo,
    )
    
    last_pct = -1
    last_frame_written = -1
    _render_start = time.time()
    
    try:
        for frame_num in range(total_frames):
            current_time = frame_num / FPS
            progress = frame_num / max(total_frames - 1, 1)
            
            # Shared overlay: glow, particles, cached text, progress bar.
            overlay = Image.fromarray(overlay_blank.copy(), 'RGBA')
            overlay = Image.alpha_composite(overlay, glow_frames[frame_num % len(glow_frames)])
            _draw_particles(overlay, particles, dt, sprite_cache)
            overlay_draw = ImageDraw.Draw(overlay)

            active_pair = None
            for appear, disappear, pair_indices in events:
                if appear <= frame_num <= disappear:
                    active_pair = pair_indices
                    break
            
            if active_pair:
                current_word_idx = None
                for line_idx in active_pair:
                    words = lyrics[line_idx].get("words", [])
                    for word_idx, word in enumerate(words):
                        if word["start"] <= current_time < word["end"]:
                            current_word_idx = (line_idx, word_idx)
                            break
                    if current_word_idx is not None:
                        break

                text_state = (tuple(active_pair), current_word_idx)
                if text_state != cached_text_state:
                    cached_text_img = Image.fromarray(overlay_blank.copy(), 'RGBA')
                    for i, line_idx in enumerate(active_pair):
                        line_data = lyrics[line_idx]
                        words = line_data.get("words", [])
                        if words:
                            _render_line_on_img(
                                cached_text_img,
                                words,
                                current_time,
                                font,
                                line_position=i,
                                total_lines=len(active_pair),
                                theme=theme,
                                style_variant=style_variant,
                                is_chorus=line_idx in chorus_line_indices,
                            )
                    cached_text_state = text_state

                if cached_text_img is not None:
                    overlay = Image.alpha_composite(overlay, cached_text_img)
                    overlay_draw = ImageDraw.Draw(overlay)
            else:
                cached_text_img = None
                cached_text_state = None

            _draw_progress_bar(
                overlay_draw,
                progress,
                progress_bg=style_variant["progress_bg"],
                progress_fg=style_variant["progress_fg"],
            )
            
            # Write overlay frame as RGBA; FFmpeg composites it over the pre-rendered background clip.
            proc.stdin.write(overlay.tobytes())
            last_frame_written = frame_num
            
            pct = int(progress * 100)
            if pct % 5 == 0 and pct != last_pct:
                elapsed_s = (frame_num + 1) / FPS
                total_s = total_frames / FPS
                fps_speed = (frame_num + 1) / max(time.time() - _render_start, 0.01)
                bar_w = 20
                filled = int(bar_w * progress)
                bar = "█" * filled + "░" * (bar_w - filled)
                print(f"    {bar} {pct:3d}% | {elapsed_s:.0f}/{total_s:.0f}s | {fps_speed:.0f} fps", flush=True)
                last_pct = pct
        
        proc.stdin.close()
        encode_timeout = max(300, int(duration * 3))
        proc.wait(timeout=encode_timeout)
        
        if proc.returncode == 0:
            size_mb = output_path.stat().st_size / (1024 * 1024)
            print(f"  ✅ SUCCESS: {output_path.name} ({size_mb:.1f} MB)")
            return True
        else:
            stderr_handle.flush()
            stderr = _tail_text_file(stderr_log_path, max_chars=5000)
            # If encoder failed and we are not already on software, retry once with software.
            if encoder != "libx264" and proc.returncode != 0:
                try:
                    proc.kill()
                except Exception:
                    pass
                stderr_handle.close()
                print(f"  ⚠️  {encoder} failed, retrying with software encoder...")
                return _retry_with_software(audio_path, lyrics_path, background_path, output_path, max_duration=max_duration)
            _set_render_error(
                "Final Encode",
                f"Encoder {encoder} failed after frame {last_frame_written + 1}/{total_frames}: {stderr}",
            )
            print(f"  ❌ FFmpeg failed: {stderr}")
            stderr_handle.close()
            return False
    
    except Exception as e:
        stderr_handle.flush()
        stderr = _tail_text_file(stderr_log_path, max_chars=5000)
        detail = f"{type(e).__name__}: {e} (last frame written: {last_frame_written + 1}/{total_frames})"
        if stderr:
            detail += f"\nFFmpeg stderr tail:\n{stderr}"
        _set_render_error("Final Encode", detail)
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        # Try fallback if we are not already on software.
        if encoder != "libx264":
            try:
                proc.kill()
            except Exception:
                pass
            stderr_handle.close()
            print(f"  ⚠️  Retrying with software encoder...")
            return _retry_with_software(audio_path, lyrics_path, background_path, output_path, max_duration=max_duration)
        stderr_handle.close()
        return False
    finally:
        try:
            stderr_handle.close()
        except Exception:
            pass
        if motion_clip_path.exists():
            try:
                motion_clip_path.unlink()
            except Exception:
                pass


def _retry_with_software(audio_path, lyrics_path, background_path, output_path, max_duration=None):
    """Fallback: re-render using software libx264 if hardware encoder failed."""
    original_fn = _detect_best_encoder
    try:
        globals()['_detect_best_encoder'] = lambda: "libx264"
        return render_with_ffmpeg(audio_path, lyrics_path, background_path, output_path, max_duration=max_duration)
    finally:
        globals()['_detect_best_encoder'] = original_fn


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
