"""
generate_background.py
Automatically generates a background image for the lyric video
based on the song's title and lyrics using Gemini API.
"""

import os
import sys
import json
import base64
import hashlib
import requests
from pathlib import Path

_PIPELINE_ROOT = str(Path(__file__).parent.parent)
if _PIPELINE_ROOT not in sys.path:
    sys.path.insert(0, _PIPELINE_ROOT)

try:
    from failure_evidence import save_error_log, save_error_context
    _HAS_EVIDENCE = True
except ImportError:
    _HAS_EVIDENCE = False


DEITY_VISUALS = {
    "shiva": {
        "subject": "Lord Shiva as the dominant central figure",
        "scene": "Mount Kailash, sacred mist, trident energy, Himalayan depth, cosmic stillness",
        "palette": "deep indigo, ash silver, icy blue, sacred amber accents",
    },
    "krishna": {
        "subject": "Lord Krishna as the dominant central figure",
        "scene": "Vrindavan mood, flute aura, peacock feather details, moonlit forest, divine romance",
        "palette": "midnight blue, peacock teal, lotus pink, warm gold",
    },
    "ram": {
        "subject": "Lord Ram as the dominant central figure",
        "scene": "heroic forest epic, divine bow, warrior calm, royal dharma, ancient India atmosphere",
        "palette": "navy blue, saffron gold, rose bronze, sacred fire highlights",
    },
    "hanuman": {
        "subject": "Lord Hanuman as the dominant central figure",
        "scene": "heroic leap, mountain wind, gada energy, strength and devotion, mythic scale",
        "palette": "burnt orange, crimson, dusky gold, stormy blue",
    },
    "ganesh": {
        "subject": "Lord Ganesha as the dominant central figure",
        "scene": "auspicious temple setting, warm lamps, sacred ornaments, gentle grandeur, blessing presence",
        "palette": "vermilion, marigold gold, ivory, temple bronze",
    },
    "durga": {
        "subject": "Goddess Durga as the dominant central figure",
        "scene": "regal divine power, lion aura, celestial battlefield serenity, goddess radiance",
        "palette": "crimson, gold, ruby, deep twilight blue",
    },
    "general": {
        "subject": "a majestic Hindu devotional focal subject",
        "scene": "ancient temple grandeur, sacred mountains, devotional atmosphere, mythic stillness",
        "palette": "deep teal, warm gold, charcoal blue, temple amber",
    },
}

CAMERA_STYLES = [
    "cinematic medium-wide framing with the deity large in frame",
    "heroic low-angle framing with a clear central silhouette",
    "slight push-in composition with rich depth behind the subject",
    "close cinematic portrait with dramatic depth and clean separation",
]

LIGHTING_STYLES = [
    "soft volumetric god-rays with controlled contrast",
    "dramatic rim light with warm sacred highlights",
    "ethereal moonlit glow with cinematic shadow depth",
    "temple-lamp illumination with rich atmospheric haze",
]

ATMOSPHERIC_DETAILS = [
    "floating particles and sacred haze",
    "subtle petals, incense smoke, and luminous dust",
    "soft bokeh depth and mystical air perspective",
    "cosmic embers and devotional mist",
]


def _seeded_choice(options, seed_key: str):
    digest = hashlib.sha256(seed_key.encode("utf-8")).digest()
    idx = int.from_bytes(digest[:4], "big") % len(options)
    return options[idx]


def _detect_deity(song_name: str, lyrics_text: str) -> str:
    haystack = f"{song_name} {lyrics_text}".lower()
    aliases = {
        "shiva": ["shiv", "shiva", "mahadev", "bholenath", "shankar"],
        "krishna": ["krishna", "kanha", "kanhaiya", "gopal", "govind", "murli"],
        "ram": ["ram", "raghunandan", "raghav", "siyaram", "raghuveer"],
        "hanuman": ["hanuman", "bajrang", "pawanputra", "maruti"],
        "ganesh": ["ganesh", "ganpati", "gajanan", "vinayak"],
        "durga": ["durga", "ambe", "amba", "jagdambe", "bhavani", "sherawali"],
    }
    for deity, words in aliases.items():
        if any(word in haystack for word in words):
            return deity
    return "general"


def _build_cover_prompt(song_name: str, lyrics_text: str) -> str:
    deity = _detect_deity(song_name, lyrics_text)
    visual = DEITY_VISUALS[deity]
    camera = _seeded_choice(CAMERA_STYLES, f"{song_name}-camera")
    lighting = _seeded_choice(LIGHTING_STYLES, f"{song_name}-lighting")
    atmosphere = _seeded_choice(ATMOSPHERIC_DETAILS, f"{song_name}-atmosphere")

    return (
        f"Cinematic devotional cover art for a lyric video. "
        f"Primary subject: {visual['subject']}. "
        f"Scene direction: {visual['scene']}. "
        f"Color palette: {visual['palette']}. "
        f"Camera: {camera}. "
        f"Lighting: {lighting}. "
        f"Atmosphere: {atmosphere}. "
        f"Keep the main face and focal subject in the upper or middle frame, "
        f"and preserve a clean uncluttered lower-third area for lyrics overlay. "
        f"Use one dominant subject only, strong silhouette readability, rich depth, mythological detail, "
        f"traditional Hindu visual language, premium cinematic finish, 16:9 landscape. "
        f"Absolutely no text, letters, symbols, captions, logos, watermarks, extra limbs, duplicate faces, or collage layout."
    )


def analyze_song_topic(song_name: str, lyrics_text: str, api_key: str = None, thumbnail_concept: str = None) -> str:
    """
    Uses Gemini text API to analyze the song and generate
    an image prompt describing the ideal background.
    """
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        print("Error: No GEMINI_API_KEY found for image prompt generation.")
        return None

    base_brief = _build_cover_prompt(song_name, lyrics_text)
    
    # If a specific thumbnail concept is provided, incorporate it prominently
    concept_section = ""
    if thumbnail_concept:
        concept_section = f"\nSpecific Visual Concept (PRIORITY):\n{thumbnail_concept}\n"

    # Take first 500 chars of lyrics for context
    lyrics_preview = lyrics_text[:500] if lyrics_text else ""
    prompt = f"""You are a senior art director creating one polished prompt for devotional cover art.

Song Title: {song_name}
Lyrics Preview: {lyrics_preview}
{concept_section}
Base Visual Brief:
{base_brief}

Instructions:
- If a 'Specific Visual Concept' is provided, use it as the primary thematic guide.
- Refine this into one premium, highly visual image-generation prompt.
- Keep it cinematic, mythological, and specifically Hindu in visual language.
- Emphasize a single dominant subject, strong focal clarity, and a clean lower-third for lyrics overlay.
- Do not mention text placement explicitly as a graphic design instruction; express it as uncluttered negative space.
- No text, no letters, no symbols, no watermark, no logo, no signage, no inscriptions, no written patterns.
- No split-screen, no collage, no multiple unrelated subjects.

Return ONLY the final prompt text.
"""

    from gemini_utils import call_gemini_api, get_gemini_text

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
    }

    result = call_gemini_api("gemini-3-flash-preview", payload, api_key=key, pool="image")
    
    if result["status"] == "success":
        image_prompt = get_gemini_text(result["data"])
        if image_prompt:
            image_prompt = image_prompt.strip()
            # Append strong no-text instruction directly to the image prompt
            image_prompt += " (STRICT RULE: Absolutely NO text, letters, words, writing, or watermarks in the image. Pure visual only.)"
            print(f"Generated image prompt: {image_prompt}")
            return image_prompt
        else:
            print(f"No text content from Gemini.")
            return None
    else:
        print(f"Gemini text API failed: {result.get('error')}")
        return base_brief + " (STRICT RULE: Absolutely NO text, letters, words, writing, or watermarks in the image. Pure visual only.)"


def generate_background_image(song_name: str, lyrics_text: str, output_path: str, api_key: str = None, thumbnail_concept: str = None) -> bool:
    """
    Generates a background image for the lyric video using Gemini's image generation.
    
    1. Analyzes the song to determine the topic/deity or uses provided concept
    2. Generates an image using Gemini imagen API
    3. Saves it to output_path
    
    Returns True on success, False on failure.
    """
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        print("Error: No GEMINI_API_KEY found.")
        return False

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Skip if image already exists for this song
    if output_path.exists():
        print(f"Background image already exists at {output_path}, skipping generation.")
        return True

    print(f"\n--- Generating Background Image for: {song_name} ---")

    # Step 1: Analyze song to get image prompt
    image_prompt = analyze_song_topic(song_name, lyrics_text, api_key=key, thumbnail_concept=thumbnail_concept)
    if not image_prompt:
        # Fallback: use song name directly
        image_prompt = f"A majestic, dark cinematic scene representing the spiritual theme of '{song_name}', with ethereal cosmic lighting, suitable as a music video background"
        print(f"Using fallback prompt: {image_prompt}")

    # Step 2: Try Nano Banana image generation models
    models_to_try = [
        "gemini-3-pro-image-preview",
        "nano-banana-pro-preview",
        "gemini-2.5-flash-image",
    ]

    from gemini_utils import call_gemini_api

    for model_name in models_to_try:
        print(f"Attempting image generation with {model_name}...")
        
        payload = {
            "contents": [{
                "parts": [{
                    "text": f"Generate a high quality, cinematic background image: {image_prompt}. (STRICT RULE: The image MUST NOT contain any text, letters, words, watermarks, or writing. It must be a purely visual background.) The image should be dark and moody, 1920x1080 landscape orientation, suitable for overlaying white text on top later."
                }]
            }],
            "generationConfig": {
                "responseModalities": ["IMAGE", "TEXT"],
                "responseMimeType": "text/plain",
            }
        }

        result = call_gemini_api(model_name, payload, api_key=key, pool="image")
        
        if result["status"] == "success":
            data = result["data"]
            candidates = data.get('candidates', [])
            if candidates:
                parts = candidates[0].get('content', {}).get('parts', [])
                for part in parts:
                    if 'inlineData' in part:
                        # Found image data
                        image_data = base64.b64decode(part['inlineData']['data'])
                        mime_type = part['inlineData'].get('mimeType', 'image/png')

                        # Determine extension
                        ext = '.png' if 'png' in mime_type else '.jpg'
                        final_path = output_path.with_suffix(ext)

                        with open(final_path, 'wb') as f:
                            f.write(image_data)

                        # If the extension changed, also copy to the expected path
                        if str(final_path) != str(output_path):
                            import shutil
                            shutil.copy2(str(final_path), str(output_path))

                        print(f"SUCCESS: Background image saved to {output_path} ({len(image_data)} bytes)")
                        return True
        else:
            print(f"Model {model_name} failed: {result.get('error')}")

    # Step 3: Fallback — try Imagen API
    print("Trying Imagen 3 API as fallback...")
    from gemini_utils import call_gemini_api
    
    imagen_payload = {
        "instances": [{"prompt": image_prompt + ". Dark moody cinematic background, 1920x1080 landscape. (STRICT NEGATIVE: NO text, NO letters, NO words, NO writing, NO watermarks)."}],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": "16:9",
        }
    }
    
    result = call_gemini_api("imagen-3.0-generate-001", imagen_payload, api_key=key, is_predict=True, pool="image")
    
    if result["status"] == "success":
        data = result["data"]
        predictions = data.get('predictions', [])
        if predictions and 'bytesBase64Encoded' in predictions[0]:
            image_data = base64.b64decode(predictions[0]['bytesBase64Encoded'])
            with open(output_path, 'wb') as f:
                f.write(image_data)
            print(f"SUCCESS (Imagen): Background image saved to {output_path}")
            return True
    else:
        print(f"Imagen API failed: {result.get('error')}")

    # Save evidence: all APIs failed for this song
    if _HAS_EVIDENCE:
        try:
            save_error_log(song_name, "background_generation", "All image generation APIs failed — using Pillow fallback")
            save_error_context(song_name, "background_generation", "All image APIs failed",
                               extra={"models_tried": [m for m in models_to_try] + ["imagen-3.0-generate-001"],
                                      "image_prompt": (image_prompt or "")[:500]})
        except Exception:
            pass

    print("WARNING: All image APIs failed. Generating dark gradient fallback background.")
    try:
        from PIL import Image, ImageDraw
        img = Image.new('RGB', (1920, 1080), (10, 10, 30))
        draw = ImageDraw.Draw(img)
        # Subtle radial-ish gradient: darker edges, slightly lighter center
        for r in range(600, 0, -5):
            brightness = int(15 + (r / 600) * 25)
            color = (brightness, brightness, brightness + 10)
            cx, cy = 960, 540
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
        img.save(str(output_path), quality=95)
        print(f"  Fallback gradient saved to {output_path}")
        return True
    except Exception as fallback_err:
        print(f"  CRITICAL: Even fallback image generation failed: {fallback_err}")
        return False


def get_lyrics_text_from_json(lyrics_path: str) -> str:
    """Extract plain text from lyrics.json for analysis."""
    try:
        with open(lyrics_path, 'r', encoding='utf-8') as f:
            lyrics = json.load(f)
        return " ".join([seg.get("text", "") for seg in lyrics if seg.get("text")])
    except Exception:
        return ""


if __name__ == "__main__":
    """Quick test: python generate_background.py --song "Lord Shiva Bhajan" """
    import argparse
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="Generate background image for lyric video")
    parser.add_argument("--song", required=True, help="Song name/title")
    parser.add_argument("--lyrics", default=None, help="Path to lyrics.json (optional)")
    parser.add_argument("--output", default="video/public/background.jpg", help="Output image path")

    args = parser.parse_args()

    lyrics_text = ""
    if args.lyrics:
        lyrics_text = get_lyrics_text_from_json(args.lyrics)

    success = generate_background_image(args.song, lyrics_text, args.output)
    if success:
        print("✅ Background image generated successfully!")
    else:
        print("❌ Failed to generate background image.")
