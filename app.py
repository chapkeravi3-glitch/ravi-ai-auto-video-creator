import streamlit as st
import requests
import os
import re
import asyncio
import tempfile
import subprocess
import imageio_ffmpeg
import edge_tts

from PIL import Image, ImageOps
from io import BytesIO


# ----------------------------------
# APP SETTINGS
# ----------------------------------

st.set_page_config(
    page_title="Ravi AI Video Creator",
    page_icon="🎬",
    layout="wide"
)

st.title("🎬 Ravi AI Video Creator")
st.write("हिंदी स्क्रिप्ट से आवाज़ और वीडियो बनाएँ।")


# ----------------------------------
# VOICE OPTIONS
# ----------------------------------

VOICES = {
    "हिंदी पुरुष": "hi-IN-MadhurNeural",
    "हिंदी महिला": "hi-IN-SwaraNeural",
}


# ----------------------------------
# SPLIT SCRIPT INTO SCENES
# ----------------------------------

def split_scenes(script):
    script = script.strip()

    if not script:
        return []

    # पहले पैराग्राफ और वाक्यों के अनुसार बाँटें
    parts = re.split(
        r"(?<=[.!?।])\s+|\n+",
        script
    )

    scenes = []

    for part in parts:
        part = part.strip()

        if not part:
            continue

        # बहुत लंबे वाक्य छोटे हिस्सों में बाँटें
        while len(part) > 350:
            cut = part.rfind(" ", 0, 300)

            if cut < 100:
                cut = 300

            scenes.append(part[:cut].strip())
            part = part[cut:].strip()

        if part:
            scenes.append(part)

    return scenes


# ----------------------------------
# SEARCH IMAGES ON WIKIMEDIA COMMONS
# ----------------------------------

def search_image(query):
    api_url = "https://commons.wikimedia.org/w/api.php"

    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}",
        "gsrnamespace": 6,
        "gsrlimit": 8,
        "prop": "imageinfo",
        "iiprop": "url",
        "iiurlwidth": 1280,
        "format": "json"
    }

    headers = {
        "User-Agent": "RaviAIVideoCreator/1.0"
    }

    try:
        response = requests.get(
            api_url,
            params=params,
            headers=headers,
            timeout=20
        )

        response.raise_for_status()
        data = response.json()

        pages = data.get("query", {}).get("pages", {})

        for page in pages.values():
            image_info = page.get("imageinfo", [])

            if not image_info:
                continue

            url = (
                image_info[0].get("thumburl")
                or image_info[0].get("url")
            )

            if url:
                return url

    except Exception:
        return None

    return None


# ----------------------------------
# DOWNLOAD IMAGE
# ----------------------------------

def get_image(url):
    headers = {
        "User-Agent": "RaviAIVideoCreator/1.0"
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=30
    )

    response.raise_for_status()

    img = Image.open(
        BytesIO(response.content)
    ).convert("RGB")

    return img


# ----------------------------------
# CREATE HINDI VOICE
# ----------------------------------

def make_voice(text, output_path, voice_name):

    async def run():
        communicate = edge_tts.Communicate(
            text,
            voice_name,
            rate="+0%"
        )

        await communicate.save(output_path)

    asyncio.run(run())

    if (
        not os.path.exists(output_path)
        or os.path.getsize(output_path) == 0
    ):
        raise RuntimeError(
            "आवाज़ की फ़ाइल नहीं बनी। इंटरनेट कनेक्शन जाँचें।"
        )


# ----------------------------------
# GET AUDIO DURATION
# ----------------------------------

def get_audio_duration(ffmpeg, audio_path):

    result = subprocess.run(
        [ffmpeg, "-i", audio_path],
        capture_output=True,
        text=True
    )

    text = result.stderr or ""

    match = re.search(
        r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
        text
    )

    if not match:
        raise RuntimeError(
            "आवाज़ की अवधि पता नहीं चली।"
        )

    hours, minutes, seconds = match.groups()

    duration = (
        int(hours) * 3600
        + int(minutes) * 60
        + float(seconds)
    )

    if duration <= 0:
        raise RuntimeError(
            "आवाज़ की अवधि सही नहीं है।"
        )

    return duration


# ----------------------------------
# CREATE VIDEO FROM IMAGES + AUDIO
# ----------------------------------

def make_video(
    images,
    audio_path,
    output_path,
    size,
    seconds
):

    if not images:
        raise RuntimeError(
            "वीडियो बनाने के लिए कोई तस्वीर नहीं मिली।"
        )

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    width, height = size

    with tempfile.TemporaryDirectory() as temp_dir:

        # आवाज़ की वास्तविक अवधि
        audio_duration = get_audio_duration(
            ffmpeg,
            audio_path
        )

        # हर तस्वीर को आवाज़ के समय के अनुसार दिखाएँ
        scene_duration = audio_duration / len(images)

        concat_path = os.path.join(
            temp_dir,
            "images.txt"
        )

        prepared = []

        # तस्वीरों को सही आकार में बदलें
        for index, img in enumerate(images):

            img = ImageOps.fit(
                img.convert("RGB"),
                (width, height),
                method=Image.Resampling.LANCZOS
            )

            path = os.path.join(
                temp_dir,
                f"scene_{index:04d}.jpg"
            )

            img.save(
                path,
                "JPEG",
                quality=90
            )

            prepared.append(path)

        # FFmpeg के लिए तस्वीरों की सूची
        with open(
            concat_path,
            "w",
            encoding="utf-8"
        ) as file:

            for path in prepared:

                safe_path = path.replace(
                    "'",
                    "'\\''"
                )

                file.write(
                    f"file '{safe_path}'\n"
                )

                file.write(
                    f"duration {scene_duration:.6f}\n"
                )

            # आखिरी तस्वीर दोबारा लिखना आवश्यक है
            safe_path = prepared[-1].replace(
                "'",
                "'\\''"
            )

            file.write(
                f"file '{safe_path}'\n"
            )

        cmd = [
            ffmpeg,
            "-y",

            "-f", "concat",
            "-safe", "0",
            "-i", concat_path,

            "-i", audio_path,

            "-map", "0:v:0",
            "-map", "1:a:0",

            "-vf",
            (
                f"scale={width}:{height}:"
                "force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                "fps=25,format=yuv420p"
            ),

            "-t",
            f"{audio_duration:.3f}",

            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",

            "-c:a", "aac",
            "-b:a", "128k",

            "-movflags", "+faststart",

            output_path
        ]

        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True
        )

    if (
        not os.path.exists(output_path)
        or os.path.getsize(output_path) == 0
    ):
        raise RuntimeError(
            "वीडियो फ़ाइल तैयार नहीं हुई।"
        )


# ----------------------------------
# USER INPUT
# ----------------------------------

script = st.text_area(
    "📝 अपनी हिंदी स्क्रिप्ट यहाँ लिखें",
    height=250,
    placeholder="यहाँ अपनी पूरी कहानी या वीडियो की स्क्रिप्ट डालें..."
)

video_format = st.selectbox(
    "📐 वीडियो का आकार चुनें",
    [
        "YouTube Shorts (9:16)",
        "Landscape (16:9)",
        "Square (1:1)"
    ]
)

voice_label = st.selectbox(
    "🎙️ हिंदी आवाज़ चुनें",
    list(VOICES.keys())
)

voice = VOICES[voice_label]
