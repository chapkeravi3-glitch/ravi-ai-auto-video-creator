import streamlit as st
import requests
import re
import os
import tempfile
import asyncio
from io import BytesIO
from PIL import Image, ImageOps
import edge_tts
import imageio_ffmpeg
import subprocess

st.set_page_config(
    page_title="Ravi AI Auto Video Creator",
    page_icon="🎬",
    layout="wide"
)

st.title("🎬 Ravi AI Auto Video Creator")
st.write("हिंदी स्क्रिप्ट डालें और तस्वीरों से वीडियो बनाएँ।")

script = st.text_area(
    "📝 अपनी पूरी हिंदी स्क्रिप्ट यहाँ डालें",
    height=220
)

video_format = st.selectbox(
    "वीडियो का आकार",
    ["YouTube Shorts (9:16)", "Landscape (16:9)", "Square (1:1)"]
)

voice = st.selectbox(
    "हिंदी आवाज़",
    ["hi-IN-MadhurNeural", "hi-IN-SwaraNeural"]
)

seconds_per_scene = st.slider(
    "हर सीन कितने सेकंड दिखे?",
    min_value=2,
    max_value=8,
    value=4
)

def split_scenes(text):
    parts = re.split(r'(?<=[।.!?])\s+|\n+', text.strip())
    return [p.strip() for p in parts if len(p.strip()) > 5]

def translate_to_english(text):
    try:
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": "en",
                "dt": "t",
                "q": text
            },
            timeout=15
        )
        r.raise_for_status()
        data = r.json()
        return " ".join(x[0] for x in data[0] if x[0])
    except Exception:
        return text

def search_image(query):
    headers = {"User-Agent": "RaviAIAutoVideoCreator/1.0"}
    english_query = translate_to_english(query)
    words = english_query.split()
    queries = [" ".join(words[:7]), " ".join(words[:3])]

    for q in queries:
        if not q.strip():
            continue
        try:
            r = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": q,
                    "gsrnamespace": 6,
                    "gsrlimit": 10,
                    "prop": "imageinfo",
                    "iiprop": "url",
                    "iiurlwidth": 1000,
                    "format": "json"
                },
                headers=headers,
                timeout=20
            )
            r.raise_for_status()
            pages = r.json().get("query", {}).get("pages", {})
            for page in pages.values():
                for info in page.get("imageinfo", []):
                    url = info.get("thumburl") or info.get("url")
                    if url:
                        return url
        except Exception:
            pass
    return None

def get_image(url):
    r = requests.get(
        url,
        headers={"User-Agent": "RaviAIAutoVideoCreator/1.0"},
        timeout=30
    )
    r.raise_for_status()
    img = Image.open(BytesIO(r.content)).convert("RGB")
    return img

def make_voice(text, output_path, voice_name):
    async def run():
        communicate = edge_tts.Communicate(
            text,
            voice_name,
            rate="+0%"
        )
        await communicate.save(output_path)
    asyncio.run(run())

def make_video(images, audio_path, output_path, size, seconds):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    width, height = size
    temp_dir = tempfile.mkdtemp()
    concat_path = os.path.join(temp_dir, "images.txt")

    prepared = []
    for index, img in enumerate(images):
        img = ImageOps.fit(
            img,
            (width, height),
            method=Image.Resampling.LANCZOS
        )
        path = os.path.join(temp_dir, f"scene_{index:03d}.jpg")
        img.save(path, "JPEG", quality=90)
        prepared.append(path)

    with open(concat_path, "w", encoding="utf-8") as f:
        for path in prepared:
            safe_path = path.replace("'", "'\\''")
            f.write(f"file '{safe_path}'\n")
            f.write(f"duration {seconds}\n")
        safe_path = prepared[-1].replace("'", "'\\''")
        f.write(f"file '{safe_path}'\n")

    cmd = [
        ffmpeg, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_path,
        "-i", audio_path,
        "-vf",
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        "zoompan=z='min(zoom+0.0008,1.08)':d=1:"
        "s=" + str(width) + "x" + str(height) + ":fps=25",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        "-movflags", "+faststart",
        output_path
    ]

    subprocess.run(cmd, check=True, capture_output=True)

if st.button("🎬 तस्वीरें खोजें और वीडियो बनाएँ", type="primary"):
    if not script.strip():
        st.warning("पहले स्क्रिप्ट डालें।")
        st.stop()

    scenes = split_scenes(script)
    if not scenes:
        st.warning("कृपया थोड़ी लंबी स्क्रिप्ट डालें।")
        st.stop()

    st.write(f"कुल सीन: {len(scenes)}")
    progress = st.progress(0)
    images = []
    successful_scenes = []

    for i, scene in enumerate(scenes, start=1):
        st.write(f"🔎 सीन {i}: {scene}")
        with st.spinner(f"सीन {i} की तस्वीर खोज रहे हैं..."):
            url = search_image(scene)
            if url:
                try:
                    img = get_image(url)
                    images.append(img)
                    successful_scenes.append(scene)
                    st.image(img, caption=f"सीन {i}", use_container_width=True)
                except Exception:
                    st.warning(f"सीन {i} की तस्वीर डाउनलोड नहीं हो सकी।")
            else:
                st.warning(f"सीन {i} की तस्वीर नहीं मिली।")
        progress.progress(i / len(scenes))

    if not images:
        st.error("कोई तस्वीर नहीं मिली। दूसरी स्क्रिप्ट या इंटरनेट कनेक्शन जाँचें।")
        st.stop()

    st.info(
        f"{len(images)} तस्वीरें मिलीं। अब हिंदी आवाज़ और वीडियो तैयार हो रहा है।"
    )

    if video_format == "YouTube Shorts (9:16)":
        size = (720, 1280)
    elif video_format == "Landscape (16:9)":
        size = (1280, 720)
    else:
        size = (720, 720)

    try:
        with tempfile.TemporaryDirectory() as workdir:
            audio_path = os.path.join(workdir, "narration.mp3")
            video_path = os.path.join(workdir, "ravi_video.mp4")

            with st.spinner("🎙️ हिंदी आवाज़ बना रहे हैं..."):
                make_voice(script, audio_path, voice)

            with st.spinner("🎬 MP4 वीडियो बना रहे हैं..."):
                make_video(
                    images,
                    audio_path,
                    video_path,
                    size,
                    seconds_per_scene
                )

            with open(video_path, "rb") as f:
                video_bytes = f.read()

            st.success("✅ वीडियो तैयार है!")
            st.video(video_bytes)
            st.download_button(
                "⬇️ MP4 वीडियो डाउनलोड करें",
                data=video_bytes,
                file_name="Ravi_AI_Video.mp4",
                mime="video/mp4",
                use_container_width=True
            )

    except Exception as e:
        st.error("वीडियो बनाते समय समस्या आई।")
        st.code(str(e)[:1500])

st.caption(
    "नोट: यह संस्करण इंटरनेट से तस्वीरें लेकर वीडियो बनाता है। "
    "AI से नई तस्वीरें बनाने की सुविधा अभी इसमें जोड़ी नहीं गई है।"
    )
