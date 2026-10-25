import streamlit as st
import requests
import subprocess
import tempfile
import os
import re
import asyncio
import random
import io
import math
import shutil
import time
from PIL import Image
import edge_tts
import imageio_ffmpeg

# requirements.txt:
# streamlit
# requests
# pillow
# edge-tts
# imageio-ffmpeg

st.set_page_config(
    page_title="Ravi AI Video Creator",
    page_icon="🎬",
    layout="wide"
)

st.title("🎬 Ravi AI Auto Video Creator")
st.write("हिंदी कहानी से तस्वीरों और आवाज़ वाला वीडियो बनाएँ (लंबे वीडियो भी)।")

VOICES = {
    "हिंदी पुरुष": "hi-IN-MadhurNeural",
    "हिंदी महिला": "hi-IN-SwaraNeural"
}

FORMATS = {
    "YouTube Shorts (9:16)": (720, 1280),
    "Landscape (16:9)": (1280, 720),
    "Square (1:1)": (720, 720)
}

FPS = 24
PAD = 0.4  # हर दृश्य के अंत में आधा सेकंड की ठहराव
HEADERS = {"User-Agent": "RaviAIVideoCreator/2.0 (personal video project)"}

script = st.text_area("📝 पूरी हिंदी कहानी लिखें", height=280)

col1, col2 = st.columns(2)
with col1:
    voice_name = st.selectbox("🎙️ आवाज़", list(VOICES.keys()))
    format_name = st.selectbox("📐 वीडियो का आकार", list(FORMATS.keys()))
with col2:
    effect = st.selectbox(
        "🎞️ फोटो इफेक्ट",
        ["Zoom In", "Zoom Out", "Pan Left to Right", "Pan Right to Left"]
    )
    max_chars = st.slider(
        "✂️ एक दृश्य में अधिकतम अक्षर", 120, 500, 260, 20
    )

keywords = st.text_input(
    "🔎 English keywords (कॉमा से अलग, हर दृश्य के लिए एक हो तो सबसे अच्छा)",
    placeholder="Ancient Babylon, hanging gardens, Babylon king, Euphrates river"
)

pixabay_key = st.text_input(
    "🔑 Pixabay API key (वैकल्पिक, मुफ़्त; ज़्यादा और बेहतर तस्वीरें मिलती हैं)",
    type="password"
)

uploaded_images = st.file_uploader(
    "अपनी तस्वीरें भी लगा सकते हैं",
    type=["jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True
)


# ---------------------------------------------------------------- दृश्य बाँटना
def chunk_sentences(text, limit):
    sentences = re.split(r"(?<=[।!?\.])\s+", text.strip())
    chunks, current = [], ""
    for s in sentences:
        if current and len(current) + len(s) + 1 > limit:
            chunks.append(current.strip())
            current = s
        else:
            current = (current + " " + s).strip()
    if current:
        chunks.append(current.strip())
    return chunks


def split_scenes(text, limit):
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [text.strip()]

    scenes = []
    for p in paragraphs:
        if len(p) > limit * 1.5:
            scenes.extend(chunk_sentences(p, limit))
        else:
            scenes.append(p)
    return [s for s in scenes if s.strip()]


# ---------------------------------------------------------------- तस्वीर खोज
def pixabay_urls(query, key):
    lang = "hi" if re.search(r"[\u0900-\u097F]", query) else "en"
    try:
        r = requests.get(
            "https://pixabay.com/api/",
            params={
                "key": key,
                "q": query[:100],
                "lang": lang,
                "image_type": "photo",
                "per_page": 30,
                "safesearch": "true",
            },
            headers=HEADERS,
            timeout=25,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        return [h.get("largeImageURL") or h.get("webformatURL") for h in hits]
    except Exception:
        return []


def wikimedia_urls(query):
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 6,
        "gsrlimit": 20,
        "prop": "imageinfo",
        "iiprop": "url|mime",
        "iiurlwidth": 1280,
    }
    for attempt in range(3):
        try:
            r = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params=params,
                headers=HEADERS,
                timeout=25,
            )
            if r.status_code == 429:
                time.sleep(2 + attempt * 2)
                continue
            r.raise_for_status()
            pages = r.json().get("query", {}).get("pages", {})
            urls = []
            for page in pages.values():
                info = page.get("imageinfo", [{}])[0]
                if info.get("mime") not in ("image/jpeg", "image/png"):
                    continue
                url = info.get("thumburl") or info.get("url")
                if url:
                    urls.append(url)
            return urls
        except Exception:
            time.sleep(1)
    return []


def find_image(queries, save_path, used, key):
    for query in queries:
        if not query:
            continue

        urls = []
        if key:
            urls += pixabay_urls(query, key)
        wiki = wikimedia_urls(query)
        random.shuffle(wiki)
        urls += wiki

        for url in urls:
            if not url or url in used:
                continue
            try:
                resp = requests.get(url, headers=HEADERS, timeout=30)
                resp.raise_for_status()
                with Image.open(io.BytesIO(resp.content)) as img:
                    if img.width < 400 or img.height < 300:
                        continue
                    img.convert("RGB").save(save_path, "JPEG", quality=90)
                used.add(url)
                return True
            except Exception:
                continue
    return False


def make_fallback_image(path, size, copy_from=None):
    """तस्वीर न मिले तो पिछली तस्वीर दोहराएँ, नहीं तो गहरा रंग।"""
    if copy_from and os.path.exists(copy_from):
        shutil.copy(copy_from, path)
    else:
        Image.new("RGB", size, (20, 24, 40)).save(path, "JPEG", quality=90)


# ---------------------------------------------------------------- आवाज़
async def make_voices(scenes, voice, folder, on_progress):
    paths = []
    for i, text in enumerate(scenes):
        path = os.path.join(folder, f"voice_{i}.mp3")
        for attempt in range(3):
            try:
                await edge_tts.Communicate(text=text, voice=voice).save(path)
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(2)
        paths.append(path)
        on_progress(i)
    return paths


def audio_duration(path, ffmpeg):
    result = subprocess.run(
        [ffmpeg, "-i", path, "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    match = re.search(
        r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr
    )
    if not match:
        raise RuntimeError("आवाज़ की अवधि नहीं मिल सकी।")
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


# ---------------------------------------------------------------- वीडियो
def motion_filter(effect, frames, w, h):
    cx = "iw/2-(iw/zoom/2)"
    cy = "ih/2-(ih/zoom/2)"

    if effect == "Zoom Out":
        z, x, y = f"1.12-0.12*on/{frames}", cx, cy
    elif effect == "Pan Left to Right":
        z = "1.12"
        x = f"(iw-iw/zoom)*on/{frames}"
        y = "(ih-ih/zoom)/2"
    elif effect == "Pan Right to Left":
        z = "1.12"
        x = f"(iw-iw/zoom)*(1-on/{frames})"
        y = "(ih-ih/zoom)/2"
    else:  # Zoom In
        z, x, y = f"1+0.12*on/{frames}", cx, cy

    return (
        f"zoompan=z='{z}':x='{x}':y='{y}':"
        f"d={frames}:s={w}x{h}:fps={FPS}"
    )


def make_clip(image, audio, out, size, effect, ffmpeg):
    w, h = size
    voice_len = audio_duration(audio, ffmpeg)
    total = voice_len + PAD
    frames = max(1, math.ceil(total * FPS))

    # zoompan से पहले थोड़ी बड़ी तस्वीर, ताकि हिलना चिकना रहे
    bw, bh = w * 3 // 2, h * 3 // 2
    vf = (
        f"scale={bw}:{bh}:force_original_aspect_ratio=increase,"
        f"crop={bw}:{bh},"
        f"{motion_filter(effect, frames, w, h)},"
        "setsar=1,format=yuv420p"
    )

    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", image,
        "-i", audio,
        "-vf", vf,
        "-af", f"apad=pad_dur={PAD}",
        "-t", f"{total:.3f}",
        "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        out,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=900
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:])


def join_clips(clips, folder, output, ffmpeg):
    list_path = os.path.join(folder, "clips.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for c in clips:
            f.write("file '{}'\n".format(c.replace("'", "'\\''")))

    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-c", "copy",
        "-movflags", "+faststart",
        output,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=1800
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:])


# ---------------------------------------------------------------- मुख्य भाग
if st.button("🎬 वीडियो बनाना शुरू करें", type="primary"):
    if not script.strip():
        st.error("पहले कहानी लिखें।")
        st.stop()

    # पिछला वीडियो साफ़ करें
    old = st.session_state.get("work_dir")
    if old and os.path.exists(old):
        shutil.rmtree(old, ignore_errors=True)
    st.session_state.pop("video_path", None)

    folder = tempfile.mkdtemp(prefix="ravi_video_")
    st.session_state["work_dir"] = folder

    try:
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        size = FORMATS[format_name]
        scenes = split_scenes(script, max_chars)
        total_scenes = len(scenes)
        st.info(f"कुल दृश्य: {total_scenes}")

        progress = st.progress(0)
        status = st.empty()

        # 1) आवाज़ (हर दृश्य की अलग)
        status.info("हिंदी आवाज़ बनाई जा रही है...")
        voices = asyncio.run(
            make_voices(
                scenes,
                VOICES[voice_name],
                folder,
                lambda i: progress.progress(
                    int((i + 1) / total_scenes * 25)
                ),
            )
        )

        # 2) तस्वीरें
        uploads = []
        for i, up in enumerate(uploaded_images or []):
            p = os.path.join(folder, f"upload_{i}.jpg")
            with Image.open(up) as img:
                img.convert("RGB").save(p, "JPEG", quality=90)
            uploads.append(p)

        kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
        used_urls = set()
        images, missing = [], []
        last_good = None

        for i, scene in enumerate(scenes):
            status.info(f"तस्वीर खोज रहे हैं: दृश्य {i + 1}/{total_scenes}")
            path = os.path.join(folder, f"scene_{i}.jpg")

            if i < len(uploads):
                images.append(uploads[i])
                last_good = uploads[i]
            else:
                words = scene.split()
                queries = []
                if kw_list:
                    n = len(kw_list)
                    queries += [kw_list[(i + j) % n] for j in range(n)]
                queries += [" ".join(words[:3]), " ".join(words[:6])]

                if find_image(queries, path, used_urls, pixabay_key):
                    images.append(path)
                    last_good = path
                else:
                    make_fallback_image(path, size, last_good)
                    images.append(path)
                    missing.append(i + 1)

            progress.progress(25 + int((i + 1) / total_scenes * 25))

        if missing:
            st.warning(
                f"इन दृश्यों की नई तस्वीर नहीं मिली: {missing}। "
                "वहाँ पिछली तस्वीर या सादा रंग लगाया गया है। "
                "बेहतर English keywords या Pixabay key आज़माएँ।"
            )

        # 3) हर दृश्य का छोटा क्लिप
        clips = []
        for i in range(total_scenes):
            status.info(f"क्लिप बन रहा है: {i + 1}/{total_scenes}")
            clip = os.path.join(folder, f"clip_{i}.mp4")
            make_clip(images[i], voices[i], clip, size, effect, ffmpeg)
            clips.append(clip)
            progress.progress(50 + int((i + 1) / total_scenes * 45))

        # 4) सब जोड़ें
        status.info("पूरा वीडियो जोड़ा जा रहा है...")
        output = os.path.join(folder, "video.mp4")
        join_clips(clips, folder, output, ffmpeg)

        # बेकार फ़ाइलें हटाएँ ताकि जगह बचे
        for c in clips:
            os.remove(c)

        progress.progress(100)
        status.empty()
        st.session_state["video_path"] = output
        st.success("वीडियो तैयार है!")

    except Exception as e:
        st.error("वीडियो बनाने में समस्या आई।")
        st.code(str(e))

# वीडियो दिखाएँ (डाउनलोड दबाने पर भी बना रहेगा)
video_path = st.session_state.get("video_path")
if video_path and os.path.exists(video_path):
    st.video(video_path)
    with open(video_path, "rb") as f:
        st.download_button(
            "⬇️ MP4 डाउनलोड करें",
            data=f,
            file_name="ravi_video.mp4",
            mime="video/mp4",
        )
