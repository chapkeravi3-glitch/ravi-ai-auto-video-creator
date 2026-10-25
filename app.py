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
from urllib.parse import quote
from PIL import Image
import edge_tts
import imageio_ffmpeg

# requirements.txt:
# streamlit
# requests
# pillow
# edge-tts
# imageio-ffmpeg
# deep-translator
# ddgs

try:
    from deep_translator import GoogleTranslator
except Exception:
    GoogleTranslator = None

try:
    from ddgs import DDGS
except Exception:
    try:
        from duckduckgo_search import DDGS
    except Exception:
        DDGS = None

st.set_page_config(
    page_title="Ravi AI Video Creator",
    page_icon="🎬",
    layout="wide"
)

st.title("🎬 Ravi AI Auto Video Creator")
st.write("सिर्फ़ हिंदी कहानी डालें। तस्वीरें अपने आप खोजकर एनिमेशन वाला वीडियो बन जाएगा।")

VOICES = {
    "हिंदी पुरुष": "hi-IN-MadhurNeural",
    "हिंदी महिला": "hi-IN-SwaraNeural"
}

FORMATS = {
    "YouTube Shorts (9:16)": (720, 1280),
    "Landscape (16:9)": (1280, 720),
    "Square (1:1)": (720, 720)
}

EFFECTS = ["Zoom In", "Zoom Out", "Pan Left to Right", "Pan Right to Left"]

FPS = 24
PAD = 0.4
FADE = 0.3
HEADERS = {"User-Agent": "RaviAIVideoCreator/3.0 (personal video project)"}

# ---------------------------------------------------------------- इनपुट
script = st.text_area("📝 पूरी हिंदी कहानी लिखें", height=280)

col1, col2 = st.columns(2)
with col1:
    voice_name = st.selectbox("🎙️ आवाज़", list(VOICES.keys()))
    format_name = st.selectbox("📐 वीडियो का आकार", list(FORMATS.keys()))
with col2:
    effect_choice = st.selectbox(
        "🎞️ फोटो इफेक्ट",
        ["Auto (हर दृश्य में अलग)"] + EFFECTS
    )
    max_chars = st.slider(
        "✂️ एक दृश्य में अधिकतम अक्षर (कम = तस्वीरें जल्दी-जल्दी बदलेंगी)",
        60, 400, 120, 10
    )

use_web = st.checkbox(
    "🌐 इंटरनेट की किसी भी तस्वीर से खोजें (ताज़ा और असली फ़ोटो मिलती हैं; "
    "इनका कॉपीराइट आपकी ज़िम्मेदारी है)",
    value=True
)
use_ai = st.checkbox(
    "🤖 तस्वीर न मिले तो AI से बनाएँ",
    value=True
)

with st.expander("और सटीक तस्वीरों के लिए (वैकल्पिक)"):
    extra_topic = st.text_input(
        "कहानी का विषय English में",
        placeholder="India cricket team 2026 match"
    )
    pixabay_key = st.text_input("Pixabay API key (मुफ़्त)", type="password")

uploaded_images = st.file_uploader(
    "अपनी तस्वीरें भी लगा सकते हैं (पहले दृश्यों में क्रम से लगेंगी)",
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
    if not paragraphs:
        paragraphs = [text.strip()]

    scenes = []
    for p in paragraphs:
        if len(p) > limit:
            scenes.extend(chunk_sentences(p, limit))
        else:
            scenes.append(p)
    return [s for s in scenes if s.strip()]


# ---------------------------------------------------------------- खोज के शब्द
STOPWORDS = set("""
the and for with that this from they them their there were was are been being have has had
not but his her him she you your our out into over under about after before then than when
where which while would could should will shall can may might also very more most some any
such only just said says like what who whom whose why how all each every both few many much
one two three four five six seven eight nine ten again once here those these its itself
""".split())


def translate_hi_en(text, limit=1500):
    if GoogleTranslator is None or not text.strip():
        return ""
    try:
        return GoogleTranslator(source="hi", target="en").translate(
            text[:limit]
        ) or ""
    except Exception:
        return ""


def english_keywords(text):
    words = re.findall(r"[A-Za-z]+", text or "")
    keep, seen = [], set()
    for w in words:
        lw = w.lower()
        if len(lw) > 3 and lw not in STOPWORDS and lw not in seen:
            seen.add(lw)
            keep.append(w)
    return keep


def llm_query(scene, topic):
    """मुफ़्त text सेवा से दृश्य के लिए तस्वीर-खोज का शब्द माँगता है।"""
    prompt = (
        "You pick real photographs for a Hindi story video. "
        f"Story topic: {topic[:300]}. "
        f"Scene (Hindi): {scene[:400]}. "
        "Reply with ONLY one English image-search query of 3 to 6 words "
        "that would find a real photograph matching this scene and topic. "
        "No quotes, no explanation."
    )
    try:
        r = requests.get(
            "https://text.pollinations.ai/" + quote(prompt),
            params={"model": "openai"},
            headers=HEADERS,
            timeout=40,
        )
        if r.status_code != 200:
            return None
        text = r.text.strip().split("\n")[0].strip(" \"'.")
        if "{" in text or not (2 <= len(text) <= 80):
            return None
        if len(text.split()) > 10:
            return None
        return text
    except Exception:
        return None


def scene_queries(scene, topic, topic_short):
    queries = []

    q = llm_query(scene, topic)
    if q:
        queries.append(q)

    english = translate_hi_en(scene)
    kws = english_keywords(english)
    if kws:
        q2 = " ".join(kws[:4])
        if topic_short:
            q2 = f"{q2} {topic_short.split()[0]}"
        queries.append(q2)

    if topic_short:
        queries.append(topic_short)

    queries.append(" ".join(scene.split()[:5]))

    seen, final = set(), []
    for q in queries:
        if q and q.lower() not in seen:
            seen.add(q.lower())
            final.append(q)
    return final, english


# ---------------------------------------------------------------- तस्वीर के स्रोत
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


def web_urls(query):
    if DDGS is None:
        return []
    try:
        with DDGS() as d:
            results = list(d.images(query, max_results=20))
        return [r.get("image") for r in results]
    except Exception:
        return []


def openverse_urls(query):
    try:
        r = requests.get(
            "https://api.openverse.org/v1/images/",
            params={"q": query, "page_size": 20, "mature": "false"},
            headers=HEADERS,
            timeout=25,
        )
        r.raise_for_status()
        urls = []
        for item in r.json().get("results", []):
            urls.append(item.get("url"))
            urls.append(item.get("thumbnail"))
        return urls
    except Exception:
        return []


def try_download(urls, save_path, used, max_tries=8):
    tries = 0
    for url in urls:
        if not url or url in used:
            continue
        if tries >= max_tries:
            break
        tries += 1
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            if len(r.content) > 15_000_000:
                continue
            with Image.open(io.BytesIO(r.content)) as img:
                if img.width < 500 or img.height < 350:
                    continue
                img.thumbnail((2000, 2000))
                img.convert("RGB").save(save_path, "JPEG", quality=90)
            used.add(url)
            return True
        except Exception:
            continue
    return False


def find_photo(queries, save_path, used, key, web_on):
    for q in queries:
        sources = []
        if key:
            sources.append(("Pixabay", lambda q=q: pixabay_urls(q, key)))
        if web_on:
            sources.append(("इंटरनेट", lambda q=q: web_urls(q)))
        sources.append(("Openverse", lambda q=q: openverse_urls(q)))

        for name, fetch in sources:
            if try_download(fetch(), save_path, used):
                return name, q
    return None, None


def ai_image(prompt, save_path, size):
    """Pollinations से तस्वीर बनवाता है (बिना key)।"""
    if not prompt.strip():
        return False

    w, h = size
    full = (
        f"{prompt}, realistic photograph, natural lighting, "
        "high detail, no text, no watermark"
    )

    for attempt in range(3):
        try:
            r = requests.get(
                "https://image.pollinations.ai/prompt/" + quote(full[:600]),
                params={
                    "width": w,
                    "height": h,
                    "nologo": "true",
                    "seed": random.randint(1, 1000000),
                },
                headers=HEADERS,
                timeout=120,
            )
            if (
                r.status_code == 200
                and r.headers.get("content-type", "").startswith("image")
            ):
                with Image.open(io.BytesIO(r.content)) as img:
                    img.convert("RGB").save(save_path, "JPEG", quality=90)
                return True
        except Exception:
            pass
        time.sleep(3 + attempt * 3)
    return False


def make_fallback_image(path, size, copy_from=None):
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


# ---------------------------------------------------------------- वीडियो क्लिप
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

    bw, bh = w * 3 // 2, h * 3 // 2
    fade_out_start = max(0.0, total - FADE)

    # CapCut जैसा: धुंधली पृष्ठभूमि + बीच में पूरी तस्वीर, फिर zoom/pan और fade
    fc = (
        "[0:v]split[a][b];"
        f"[a]scale={bw}:{bh}:force_original_aspect_ratio=increase,"
        f"crop={bw}:{bh},boxblur=25:5[bg];"
        f"[b]scale={bw}:{bh}:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        f"{motion_filter(effect, frames, w, h)},"
        f"fade=t=in:st=0:d={FADE},"
        f"fade=t=out:st={fade_out_start:.3f}:d={FADE},"
        "setsar=1,format=yuv420p[v]"
    )

    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", image,
        "-i", audio,
        "-filter_complex", fc,
        "-map", "[v]",
        "-map", "1:a",
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

    old = st.session_state.get("work_dir")
    if old and os.path.exists(old):
        shutil.rmtree(old, ignore_errors=True)
    st.session_state.pop("video_path", None)
    st.session_state.pop("gallery", None)

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

        # 2) कहानी का विषय (तस्वीरों को सही दिशा देने के लिए)
        status.info("कहानी का विषय समझा जा रहा है...")
        if extra_topic.strip():
            topic = extra_topic.strip()
        else:
            topic = translate_hi_en(script, 600) or script[:200]
        topic_short = " ".join(english_keywords(topic)[:4])

        # 3) हर दृश्य की तस्वीर
        uploads = []
        for i, up in enumerate(uploaded_images or []):
            p = os.path.join(folder, f"upload_{i}.jpg")
            with Image.open(up) as img:
                img.convert("RGB").save(p, "JPEG", quality=90)
            uploads.append(p)

        used_urls = set()
        images, gallery = [], []
        missing = []
        last_good = None

        for i, scene in enumerate(scenes):
            status.info(f"तस्वीर खोज रहे हैं: दृश्य {i + 1}/{total_scenes}")
            path = os.path.join(folder, f"scene_{i}.jpg")
            source, query = None, ""

            if i < len(uploads):
                images.append(uploads[i])
                last_good = uploads[i]
                source, query = "आपकी तस्वीर", ""
                shutil.copy(uploads[i], path)
            else:
                queries, english = scene_queries(scene, topic, topic_short)
                source, query = find_photo(
                    queries, path, used_urls, pixabay_key.strip(), use_web
                )

                if not source and use_ai:
                    status.info(
                        f"तस्वीर नहीं मिली, AI से बना रहे हैं: दृश्य {i + 1}"
                    )
                    prompt = english or (queries[0] if queries else "")
                    if ai_image(prompt, path, size):
                        source, query = "AI", prompt[:80]

                if source:
                    images.append(path)
                    last_good = path
                else:
                    make_fallback_image(path, size, last_good)
                    images.append(path)
                    missing.append(i + 1)
                    source, query = "खाली", ""

            gallery.append(
                {"scene": i + 1, "source": source, "query": query, "path": path}
            )
            progress.progress(25 + int((i + 1) / total_scenes * 25))
            time.sleep(0.3)

        if missing:
            st.warning(
                f"इन दृश्यों की तस्वीर नहीं मिली: {missing}। "
                "वहाँ पिछली तस्वीर या सादा रंग लगाया गया है।"
            )

        # 4) हर दृश्य का एनिमेटेड क्लिप
        offset = random.randint(0, len(EFFECTS) - 1)
        clips = []
        for i in range(total_scenes):
            status.info(f"क्लिप बन रहा है: {i + 1}/{total_scenes}")
            if effect_choice.startswith("Auto"):
                eff = EFFECTS[(i + offset) % len(EFFECTS)]
            else:
                eff = effect_choice

            clip = os.path.join(folder, f"clip_{i}.mp4")
            make_clip(images[i], voices[i], clip, size, eff, ffmpeg)
            clips.append(clip)
            progress.progress(50 + int((i + 1) / total_scenes * 45))

        # 5) सब जोड़ें
        status.info("पूरा वीडियो जोड़ा जा रहा है...")
        output = os.path.join(folder, "video.mp4")
        join_clips(clips, folder, output, ffmpeg)

        for c in clips:
            os.remove(c)

        progress.progress(100)
        status.empty()
        st.session_state["video_path"] = output
        st.session_state["gallery"] = gallery
        st.success("वीडियो तैयार है!")

    except Exception as e:
        st.error("वीडियो बनाने में समस्या आई।")
        st.code(str(e))

# ---------------------------------------------------------------- नतीजा
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

gallery = st.session_state.get("gallery")
if gallery:
    with st.expander("किस दृश्य में कौन-सी तस्वीर लगी"):
        for g in gallery:
            if os.path.exists(g["path"]):
                st.image(
                    g["path"],
                    width=180,
                    caption=f"दृश्य {g['scene']} | {g['source']} | {g['query']}",
                )
