"""
generate_background.py
Automatically generates a background image for the lyric video
based on the song's title and lyrics using Gemini API.
"""

import os
import json
import base64
import requests
from pathlib import Path


def analyze_song_topic(song_name: str, lyrics_text: str, api_key: str = None) -> str:
    """
    Uses Gemini text API to analyze the song and generate
    an image prompt describing the ideal background.
    """
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        print("Error: No GEMINI_API_KEY found for image prompt generation.")
        return None

    # Take first 500 chars of lyrics for context
    lyrics_preview = lyrics_text[:500] if lyrics_text else ""

    prompt = f"""You are a visual art director for Indian devotional music videos.

Given this song information, generate a SHORT image prompt (max 2 sentences) describing the perfect background image for a lyric video.

Song Title: {song_name}
Lyrics Preview: {lyrics_preview}

RULES:
1. Identify the deity or spiritual theme (Shiva, Krishna, Ganesh, Ram, Hanuman, Durga, etc.)
2. Describe a majestic, cinematic scene featuring that deity or theme
3. Use dark, moody tones suitable for text overlay (dark backgrounds work best)
4. Include atmospheric elements (cosmic, ethereal lighting, sacred symbols)
5. Keep the prompt under 2 sentences
6. If you cannot identify a specific deity, describe a generic spiritual/devotional scene

Return ONLY the image prompt text, nothing else."""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
    headers = {"Content-Type": "application/json"}
    data = {
        "contents": [{"parts": [{"text": prompt}]}],
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            result = response.json()
            image_prompt = result['candidates'][0]['content']['parts'][0]['text'].strip()
            print(f"Generated image prompt: {image_prompt}")
            return image_prompt
        else:
            print(f"Gemini text API failed: {response.status_code}")
            return None
    except Exception as e:
        print(f"Error analyzing song topic: {e}")
        return None


def generate_background_image(song_name: str, lyrics_text: str, output_path: str, api_key: str = None) -> bool:
    """
    Generates a background image for the lyric video using Gemini's image generation.
    
    1. Analyzes the song to determine the topic/deity
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
    image_prompt = analyze_song_topic(song_name, lyrics_text, api_key=key)
    if not image_prompt:
        # Fallback: use song name directly
        image_prompt = f"A majestic, dark cinematic scene representing the spiritual theme of '{song_name}', with ethereal cosmic lighting, suitable as a music video background"
        print(f"Using fallback prompt: {image_prompt}")

    # Step 2: Try Nano Banana image generation models
    # Nano Banana Pro = gemini-3-pro-image-preview (best quality)
    # Nano Banana = gemini-2.5-flash-image (faster, good quality)
    models_to_try = [
        "gemini-3-pro-image-preview",
        "gemini-2.5-flash-image",
        "gemini-2.0-flash-exp-image-generation",
    ]

    for model_name in models_to_try:
        print(f"Attempting image generation with {model_name}...")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
        headers = {"Content-Type": "application/json"}
        data = {
            "contents": [{
                "parts": [{
                    "text": f"Generate a high quality, cinematic background image: {image_prompt}. The image should be dark and moody, 1920x1080 landscape orientation, suitable for overlaying white text on top."
                }]
            }],
            "generationConfig": {
                "responseModalities": ["IMAGE", "TEXT"],
                "responseMimeType": "text/plain",
            }
        }

        try:
            response = requests.post(url, headers=headers, json=data, timeout=120)
            if response.status_code == 200:
                result = response.json()
                candidates = result.get('candidates', [])
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

                    print(f"No image data in response from {model_name}")
            else:
                print(f"Model {model_name} failed: {response.status_code} - {response.text[:200]}")
        except Exception as e:
            print(f"Error with {model_name}: {e}")

    # Step 3: Fallback — try Imagen API
    print("Trying Imagen 3 API as fallback...")
    try:
        imagen_url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-4.0-generate-001:predict?key={key}"
        imagen_data = {
            "instances": [{"prompt": image_prompt + ". Dark moody cinematic background, 1920x1080 landscape, suitable for text overlay."}],
            "parameters": {
                "sampleCount": 1,
                "aspectRatio": "16:9",
            }
        }
        response = requests.post(imagen_url, headers=headers, json=imagen_data, timeout=120)
        if response.status_code == 200:
            result = response.json()
            predictions = result.get('predictions', [])
            if predictions and 'bytesBase64Encoded' in predictions[0]:
                image_data = base64.b64decode(predictions[0]['bytesBase64Encoded'])
                with open(output_path, 'wb') as f:
                    f.write(image_data)
                print(f"SUCCESS (Imagen): Background image saved to {output_path}")
                return True
            else:
                print(f"No image data from Imagen API")
        else:
            print(f"Imagen API failed: {response.status_code} - {response.text[:200]}")
    except Exception as e:
        print(f"Imagen fallback error: {e}")

    print("WARNING: Could not generate background image. Video will use gradient fallback.")
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
