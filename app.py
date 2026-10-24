import streamlit as st
import requests
import subprocess
import tempfile
import os
import re
import asyncio
import json
import random
from pathlib import Path
from PIL import Image, ImageDraw
import edge_tts
import imageio_ffmpeg

st.set_page_config(
    page_title="Ravi AI Auto Video Creator",
    page_icon="🎬",
    layout="wide"
)

st.title("🎬 Ravi AI Auto Video Creator")
st.write("हिंदी स्क्रिप्ट से आवाज़ और फोटो इफेक्ट वाला वीडियो बनाएँ।")

VOICES = {
    "हिंदी पुरुष": "hi-IN-MadhurNeural",
    "हिंदी महिला": "hi-IN-SwaraNeural",
}

FORMATS = {
    "YouTube Shorts (9:16)": (720, 1280),
    "YouTube Landscape (16:9)": (1280, 720),
    "Square (1:1)": (720, 720),
}

with st.expander("ℹ️ ऐप की जानकारी"):
    st.write(
        "यह ऐप हिंदी आवाज़, ऑनलाइन तस्वीरें और फोटो पर "
        "Zoom/Pan जैसे इफेक्ट लगाकर MP4 वीडियो बनाती है। "
        "यह वास्तविक AI-generated moving people नहीं बनाती।"
    )

script = st.text_area(
    "📝 अपनी हिंदी स्क्रिप्ट लिखें",
    height=260,
    placeholder="यहाँ अपनी पूरी कहानी या वीडियो की स्क्रिप्ट डालें..."
)

voice_label = st.selectbox(
    "🎙️ आवाज़ चुनें",
    list(VOICES.keys())
)

video_format = st.selectbox(
    "📐 वीडियो का आकार",
    list(FORMATS.keys())
)

effect = st.selectbox(
    "🎞️ फोटो इफेक्ट",
    [
        "धीमा Zoom In",
        "धीमा Zoom Out",
        "बाएँ से दाएँ Pan",
        "दाएँ से बाएँ Pan",
        "हल्का Rotation"
    ]
)

fps = st.selectbox("वीडियो FPS", [24, 30], index=0)

search_terms = st.text_input(
    "🔎 तस्वीर खोजने के लिए English keywords (वैकल्पिक)",
    placeholder="ancient Babylon, historical city, desert"
)

uploaded_images = st.file_uploader(
    "🖼️ चाहें तो अपनी तस्वीरें अपलोड करें",
    type=["jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True
)

def split_script(text):
    paragraphs = [
        p.strip()
        for p in re.split(r"\n\s*\n", text)
        if p.strip()
    ]

    if len(paragraphs) > 1:
        return paragraphs

    sentences = re.split(r"(?<=[।!?])\s+", text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    scenes = []
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) > 350 and current:
            scenes.append(current.strip())
            current = sentence
        else:
            current = (current + " " + sentence).strip()

    if current:
        scenes.append(current)

    return scenes or [text.strip()]


def make_placeholder(path, text, size):
    image = Image.new("RGB", size, (30, 35, 50))
    draw = ImageDraw.Draw(image)

    words = text[:160]
    lines = []
    for i in range(0, len(words), 28):
        lines.append(words[i:i + 28])

    y = size[1] // 3
    for line in lines[:5]:
        draw.text((30, y), line, fill="white")
        y += 30

    image.save(path, "JPEG")


def search_commons_image(query, output_path):
    if not query.strip():
        return False

    api = "https://commons.wikimedia.org/w/api.php"

    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query + " filetype:bitmap",
        "gsrnamespace": 6,
        "gsrlimit": 10,
        "prop": "imageinfo",
        "iiprop": "url",
        "iiurlwidth": 1200,
    }

    headers = {
        "User-Agent": "RaviAIVideoCreator/1.0 (educational video project)"
    }

    try:
        response = requests.get(
            api,
            params=params,
            headers=headers,
            timeout=20
        )
        response.raise_for_status()
        data = response.json()

        pages = data.get("query", {}).get("pages", {})
        candidates = list(pages.values())
        random.shuffle(candidates)

        for page in candidates:
            info = page.get("imageinfo", [{}])[0]
            url = info.get("thumburl") or info.get("url")

            if not url:
                continue

            image_response = requests.get(
                url,
                headers=headers,
                timeout=25
            )
            image_response.raise_for_status()

            with Image.open(
                __import__("io").BytesIO(image_response.content)
            ) as img:
                img.convert("RGB").save(
                    output_path,
                    "JPEG",
                    quality=90
                )

            return True

    except Exception:
        return False

    return False


async def create_voice(text, voice, output_path):
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate="+0%",
        volume="+0%"
    )
    await communicate.save(output_path)


def get_duration(audio_path, ffmpeg):
    command = [
        ffmpeg,
        "-i", audio_path,
        "-f", "null",
        "-"
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    match = re.search(
        r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
        result.stderr
    )

    if not match:
        raise RuntimeError("ऑडियो की अवधि पता नहीं चल सकी।")

    hours, minutes, seconds = match.groups()

    return (
        int(hours) * 3600
        + int(minutes) * 60
        + float(seconds)
    )


def render_video(images, audio_path, output_path, size, fps, effect):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    width, height = size
    duration = get_duration(audio_path, ffmpeg)

    count = len(images)
    scene_duration = duration / count

    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]

    for image_path in images:
        command.extend([
            "-loop", "1",
            "-framerate", str(fps),
            "-t", str(scene_duration),
            "-i", image_path
        ])

    command.extend(["-i", audio_path])

    filters = []

    for i in range(count):
        if effect == "धीमा Zoom Out":
            zoom = (
                f"zoompan=z='max(1.0,1.12-0.12*on/"
                f"{max(1, int(scene_duration * fps))})'"
            )
        elif effect == "बाएँ से दाएँ Pan":
            zoom = (
                f"zoompan=z='1.12':"
                f"x='(iw-iw/zoom)*on/"
                f"{max(1, int(scene_duration * fps))}'"
            )
        elif effect == "दाएँ से बाएँ Pan":
            zoom = (
                f"zoompan=z='1.12':"
                f"x='(iw-iw/zoom)*(1-on/"
                f"{max(1, int(scene_duration * fps))})'"
            )
        elif effect == "हल्का Rotation":
            zoom = "rotate='0.015*sin(2*PI*t/4)':c=black"
        else:
            zoom = (
                f"zoompan=z='min(zoom+0.001,1.12)'"
            )

        if effect in ["बाएँ से दाएँ Pan", "दाएँ से बाएँ Pan"]:
            zoom += f":y='(ih-ih/zoom)/2':d=1:s={width}x{height}:fps={fps}"
        elif effect == "हल्का Rotation":
            zoom = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},{zoom},"
                f"scale={width}:{height},setsar=1,fps={fps}"
            )
        else:
            zoom += f":d=1:s={width}x{height}:fps={fps}"

        if effect != "हल्का Rotation":
            base = (
                f"scale={width}:{height}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={width}:{height},"
            )
            filters.append(
                f"[{i}:v]{base}{zoom},setsar=1,format=yuv420p[v{i}]"
            )
        else:
            filters.append(
                f"[{i}:v]{zoom},setsar=1,format=yuv420p[v{i}]"
            )

    concat_inputs = "".join(f"[v{i}]" for i in range(count))
    filters.append(
        f"{concat_inputs}concat=n={count}:v=1:a=0[vout]"
    )

    filter_complex = ";".join(filters)

    command.extend([
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", f"{count}:a",
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        output_path
    ])

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2500:])


if st.button("🎬 वीडियो बनाना शुरू करें", type="primary"):
    if not script.strip():
        st.error("पहले अपनी स्क्रिप्ट लिखें।")
        st.stop()

    progress = st.progress(0)
    status = st.empty()

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            scenes = split_script(script)
            images = []

            # अपलोड की गई तस्वीरें पहले इस्तेमाल होंगी।
            upload_paths = []

            for i, uploaded in enumerate(uploaded_images or []):
                path = os.path.join(temp_dir, f"upload_{i}.jpg")

                with Image.open(uploaded) as img:
                    img.convert("RGB").save(path, "JPEG", quality=90)

                upload_paths.append(path)

            status.info("1/4: तस्वीरें तैयार हो रही हैं...")

            for i, scene in enumerate(scenes):
                path = os.path.join(temp_dir, f"scene_{i}.jpg")

                if i < len(upload_paths):
                    images.append(upload_paths[i])
                else:
                    query = search_terms.strip()

                    if not query:
                        # स्क्रिप्ट के शुरुआती शब्द खोजने की कोशिश।
                        query = " ".join(scene.split()[:5])

                    found = search_commons_image(query, path)

                    if not found:
                        make_placeholder(path, scene, (900, 600))

                    images.append(path)

                progress.progress(
                    min(25, int(25 * (i + 1) / len(scenes)))
                )

            status.info("2/4: हिंदी आवाज़ बन रही है...")

            audio_path = os.path.join(temp_dir, "narration.mp3")

            asyncio.run(
                create_voice(
                    script,
                    VOICES[voice_label],
                    audio_path
                )
            )

            progress.progress(40)
            status.info("3/4: वीडियो रेंडर हो रहा है...")

            output_path = os.path.join(temp_dir, "ravi_video.mp4")

            render_video(
                images,
                audio_path,
                output_path,
                FORMATS[video_format],
                fps,
                effect
            )

            progress.progress(100)
            status.success("वीडियो तैयार है!")

            with open(output_path, "rb") as video_file:
                video_bytes = video_file.read()

            st.video(video_bytes)

            st.download_button(
                "⬇️ MP4 वीडियो डाउनलोड करें",
                data=video_bytes,
                file_name="ravi_ai_video.mp4",
                mime="video/mp4",
                type="primary"
            )

            st.info(
                "ध्यान दें: Wikimedia Commons की तस्वीरों के लाइसेंस "
                "और attribution की शर्तें जाँचें। कुछ दृश्यों में "
                "तस्वीर न मिलने पर टेक्स्ट वाली तस्वीर बनेगी।"
            )

    except Exception as error:
        st.error("वीडियो नहीं बन पाया।")
        st.code(str(error))
        st.info(
            "इंटरनेट कनेक्शन जाँचें। बहुत लंबी स्क्रिप्ट को "
            "पहले छोटे हिस्से में आज़माएँ।"
                 )
