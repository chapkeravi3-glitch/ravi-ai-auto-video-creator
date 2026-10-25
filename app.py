import streamlit as st
import requests
import subprocess
import tempfile
import os
import re
import asyncio
import random
import io
from PIL import Image, ImageDraw
import edge_tts
import imageio_ffmpeg

st.set_page_config(
    page_title="Ravi AI Video Creator",
    page_icon="🎬",
    layout="wide"
)

st.title("🎬 Ravi AI Auto Video Creator")
st.write("हिंदी कहानी से तस्वीरों और आवाज़ वाला वीडियो बनाएँ।")

VOICES = {
    "हिंदी पुरुष": "hi-IN-MadhurNeural",
    "हिंदी महिला": "hi-IN-SwaraNeural"
}

FORMATS = {
    "YouTube Shorts (9:16)": (720, 1280),
    "Landscape (16:9)": (1280, 720),
    "Square (1:1)": (720, 720)
}

script = st.text_area(
    "📝 पूरी हिंदी कहानी लिखें",
    height=280
)

voice_name = st.selectbox("🎙️ आवाज़", list(VOICES.keys()))
format_name = st.selectbox("📐 वीडियो का आकार", list(FORMATS.keys()))

effect = st.selectbox(
    "🎞️ फोटो इफेक्ट",
    ["Zoom In", "Zoom Out", "Pan Left to Right", "Pan Right to Left"]
)

keywords = st.text_input(
    "🔎 वैकल्पिक English keywords",
    placeholder="Ancient Babylon, historical city"
)

uploaded_images = st.file_uploader(
    "अपनी तस्वीरें भी लगा सकते हैं",
    type=["jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True
)


def split_scenes(text):
    paragraphs = [
        p.strip()
        for p in re.split(r"\n\s*\n", text)
        if p.strip()
    ]

    if len(paragraphs) > 1:
        return paragraphs

    sentences = re.split(r"(?<=[।!?])\s+", text.strip())
    scenes = []
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) > 260 and current:
            scenes.append(current.strip())
            current = sentence
        else:
            current = (current + " " + sentence).strip()

    if current:
        scenes.append(current)

    return scenes or [text.strip()]


def search_images(query, save_path):
    api = "https://commons.wikimedia.org/w/api.php"

    headers = {
        "User-Agent": "RaviAIVideoCreator/1.0 (personal video project)"
    }

    queries = [query]

    # वैकल्पिक keywords से तस्वीर न मिले तो दृश्य के शब्द भी आजमाएँ।
    if query:
        queries.append(" ".join(query.split()[:3]))

    for search_query in queries:
        try:
            params = {
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": search_query,
                "gsrnamespace": 6,
                "gsrlimit": 15,
                "prop": "imageinfo",
                "iiprop": "url",
                "iiurlwidth": 1200
            }

            response = requests.get(
                api,
                params=params,
                headers=headers,
                timeout=25
            )
            response.raise_for_status()

            pages = response.json().get("query", {}).get("pages", {})
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
                    io.BytesIO(image_response.content)
                ) as img:
                    img.convert("RGB").save(
                        save_path,
                        "JPEG",
                        quality=90
                    )

                return True

        except Exception:
            continue

    return False


async def make_voice(text, voice, path):
    communicator = edge_tts.Communicate(
        text=text,
        voice=voice
    )
    await communicator.save(path)


def audio_duration(path, ffmpeg):
    result = subprocess.run(
        [ffmpeg, "-i", path, "-f", "null", "-"],
        capture_output=True,
        text=True
    )

    match = re.search(
        r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
        result.stderr
    )

    if not match:
        raise RuntimeError("आवाज़ की अवधि नहीं मिल सकी।")

    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def create_video(images, audio, output, size, effect):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    width, height = size
    duration = audio_duration(audio, ffmpeg)

    scene_count = len(images)
    scene_duration = duration / scene_count
    fps = 24

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]

    for path in images:
        cmd += [
            "-loop", "1",
            "-framerate", str(fps),
            "-t", str(scene_duration),
            "-i", path
        ]

    cmd += ["-i", audio]

    filters = []

    for i in range(scene_count):
        base = (
            f"scale={width}:{height}:"
            "force_original_aspect_ratio=increase,"
            f"crop={width}:{height}"
        )

        frames = max(1, int(scene_duration * fps))

        if effect == "Zoom Out":
            motion = (
                f"zoompan=z='max(1.0,1.12-0.12*on/{frames})':"
                f"d=1:s={width}x{height}:fps={fps}"
            )
        elif effect == "Pan Left to Right":
            motion = (
                f"zoompan=z='1.12':"
                f"x='(iw-iw/zoom)*on/{frames}':"
                f"y='(ih-ih/zoom)/2':"
                f"d=1:s={width}x{height}:fps={fps}"
            )
        elif effect == "Pan Right to Left":
            motion = (
                f"zoompan=z='1.12':"
                f"x='(iw-iw/zoom)*(1-on/{frames})':"
                f"y='(ih-ih/zoom)/2':"
                f"d=1:s={width}x{height}:fps={fps}"
            )
        else:
            motion = (
                f"zoompan=z='min(zoom+0.001,1.12)':"
                f"d=1:s={width}x{height}:fps={fps}"
            )

        filters.append(
            f"[{i}:v]{base},{motion},"
            f"setsar=1,format=yuv420p[v{i}]"
        )

    joined = "".join(f"[v{i}]" for i in range(scene_count))

    filters.append(
        f"{joined}concat=n={scene_count}:v=1:a=0[vout]"
    )

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]",
        "-map", f"{scene_count}:a",
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        "-movflags", "+faststart",
        output
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2500:])


if st.button("🎬 वीडियो बनाना शुरू करें", type="primary"):
    if not script.strip():
        st.error("पहले कहानी लिखें।")
        st.stop()

    try:
        with tempfile.TemporaryDirectory() as folder:
            scenes = split_scenes(script)
            images = []
            failed = []
            progress = st.progress(0)
            status = st.empty()

            uploads = []

            for i, uploaded in enumerate(uploaded_images or []):
                path = os.path.join(folder, f"upload_{i}.jpg")
                with Image.open(uploaded) as img:
                    img.convert("RGB").save(path, "JPEG")
                uploads.append(path)

            # हर दृश्य के लिए अलग तस्वीर
            for i, scene in enumerate(scenes):
                status.info(
                    f"तस्वीर खोज रहे हैं: दृश्य {i + 1}/{len(scenes)}"
                )

                if i < len(uploads):
                    images.append(uploads[i])
                else:
                    path = os.path.join(folder, f"scene_{i}.jpg")

                    # keywords के अलग हिस्से अलग दृश्यों के लिए इस्तेमाल हों।
                    keyword_parts = keywords.split(",")
                    if keyword_parts:
                        query = keyword_parts[i % len(keyword_parts)].strip()
                    else:
                        query = ""

                    if not query:
                        query = " ".join(scene.split()[:6])

                    found = search_images(query, path)

                    if found:
                        images.append(path)
                    else:
                        failed.append(i + 1)

                progress.progress(
                    int((i + 1) / len(scenes) * 45)
                )

            if not images:
                st.error(
                    "एक भी तस्वीर डाउनलोड नहीं हुई। "
                    "इंटरनेट या Wikimedia Commons की पहुँच जाँचें।"
                )
                st.stop()

            if failed:
                st.warning(
                    f"इन दृश्यों की तस्वीर नहीं मिली: {failed}. "
                    "वीडियो में केवल मिली हुई तस्वीरें रहेंगी।"
                )

            status.info("हिंदी आवाज़ बनाई जा रही है...")
            audio = os.path.join(folder, "voice.mp3")

            asyncio.run(
                make_voice(script, VOICES[voice_name], audio)
            )

            status.info("वीडियो बनाया जा रहा है...")
            output = os.path.join(folder, "video.mp4")

            create_video(
                images,
                audio,
                output,
                FORMATS[format_name],
                effect
            )

            with open(output, "rb") as f:
                video_bytes = f.read()

            st.success("वीडियो तैयार है!")
            st.video(video_bytes)

            st.download_button(
                "⬇️ MP4 डाउनलोड करें",
                data=video_bytes,
                file_name="ravi_video.mp4",
                mime="video/mp4"
            )

    except Exception as e:
        st.error("वीडियो बनाने में समस्या आई।")
        st.code(str(e))
