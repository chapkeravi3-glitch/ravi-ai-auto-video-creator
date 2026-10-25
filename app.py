import streamlit as st
import requests
import subprocess
import tempfile
import os
import re
import json
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
st.write("सिर्फ़ हिंदी कहानी डालें। कहानी के हिसाब से तस्वीरें चुनकर एनिमेशन वाला वीडियो बनेगा।")

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
# मुफ़्त स्तर वाले मॉडल, क्रम से आज़माए जाते हैं (नाम बदल सकते हैं)
GEMINI_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3-flash-preview"]
HEADERS = {"User-Agent": "RaviAIVideoCreator/4.0 (personal video project)"}

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
        60, 400, 140, 10
    )

use_web = st.checkbox(
    "🌐 इंटरनेट की तस्वीरों में खोजें (कॉपीराइट आपकी ज़िम्मेदारी है)",
    value=True
)
use_ai = st.checkbox(
    "🤖 मेल खाती तस्वीर न मिले तो AI से बनाएँ (बेमेल तस्वीर लगाने से यह बेहतर है)",
    value=True
)

st.markdown("**सबसे सटीक तस्वीरों के लिए:**")
gemini_key = st.text_input(
    "Google Gemini API key (मुफ़्त; aistudio.google.com से मिलती है; सबसे सटीक तस्वीरों के लिए सुझाई जाती है)",
    type="password"
)

with st.expander("और विकल्प (वैकल्पिक)"):
    extra_topic = st.text_input(
        "कहानी का मुख्य विषय English में (हर खोज में जुड़ेगा)",
        placeholder="Babylon"
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

GENERIC = set(
    "ancient city people photo image picture view history historical old world great".split()
)


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


def parse_json(text):
    text = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def plan_with_ai(scenes, opening, key, on_progress=None):
    """हर दृश्य के लिए तस्वीर की खोज और AI prompt, मुफ़्त Gemini से।"""
    anchor = ""
    plans = [None] * len(scenes)
    last_error = ""
    CH = 15

    for start in range(0, len(scenes), CH):
        chunk = scenes[start:start + CH]
        numbered = "\n".join(
            f"{start + j + 1}. {s[:300]}" for j, s in enumerate(chunk)
        )
        prompt = (
            "You choose real photographs for a Hindi documentary-style video.\n"
            f"Story opening (Hindi): {opening[:700]}\n\n"
            "Scenes (Hindi), numbered:\n"
            f"{numbered}\n\n"
            "For EACH scene return:\n"
            '- "query": an English image-search query of 2 to 5 words for a REAL '
            "photograph, museum object, ruin, map or painting that visually matches "
            "what is being said. Always include the specific subject name "
            "(place, person, object) - never only generic words.\n"
            '- "ai": a detailed English prompt (one sentence) describing the '
            "visual, to generate an image if no photo is found. No text in the "
            "image, no real living people.\n"
            'Also return "anchor": the one or two word main subject of the whole story.\n'
            "Respond with ONLY JSON, no markdown: "
            '{"anchor":"...","scenes":[{"n":1,"query":"...","ai":"..."}]}'
        )

        data = None
        for attempt in range(2):
            try:
                text = ""
                for model in GEMINI_MODELS:
                    r = requests.post(
                        "https://generativelanguage.googleapis.com/v1beta/"
                        f"models/{model}:generateContent",
                        headers={
                            "x-goog-api-key": key,
                            "Content-Type": "application/json",
                        },
                        json={
                            "contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {
                                "responseMimeType": "application/json"
                            },
                        },
                        timeout=90,
                    )
                    if r.status_code == 200:
                        parts = r.json()["candidates"][0]["content"]["parts"]
                        text = "".join(p.get("text", "") for p in parts)
                        break
                    last_error = f"{model} {r.status_code}: {r.text[:150]}"
                    if r.status_code == 429:
                        time.sleep(6)
                data = parse_json(text)
                if data:
                    break
            except Exception as e:
                last_error = str(e)[:200]

        if on_progress:
            on_progress(min(start + CH, len(scenes)))
        time.sleep(1)

        if not data:
            continue

        anchor = anchor or str(data.get("anchor", "")).strip()
        for item in data.get("scenes", []):
            try:
                n = int(item.get("n")) - 1
            except Exception:
                continue
            if 0 <= n < len(scenes):
                plans[n] = item

    return anchor, plans, last_error


def llm_query(scene, topic):
    """Gemini key न हो तो मुफ़्त text सेवा से कोशिश।"""
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


def with_anchor(query, anchor):
    if anchor and anchor.lower() not in query.lower():
        return f"{anchor} {query}"
    return query


def scene_queries(scene, topic, anchor):
    queries = []

    q = llm_query(scene, topic)
    if q:
        queries.append(with_anchor(q, anchor))

    english = translate_hi_en(scene)
    kws = english_keywords(english)
    if kws:
        queries.append(with_anchor(" ".join(kws[:3]), anchor))

    if anchor:
        queries.append(anchor)

    seen, final = set(), []
    for q in queries:
        if q and q.lower() not in seen:
            seen.add(q.lower())
            final.append(q)
    return final, english


# ---------------------------------------------------------------- मेल की जाँच
def match_words(query, anchor):
    words = re.findall(r"[a-z]+", f"{query} {anchor}".lower())
    strict = {
        w for w in words
        if len(w) >= 4 and w not in STOPWORDS and w not in GENERIC
    }
    if strict:
        return strict
    return {w for w in words if len(w) >= 4 and w not in STOPWORDS}


def score_text(text, words):
    t = (text or "").lower()
    return sum(1 for w in words if w in t)


# ---------------------------------------------------------------- तस्वीर के स्रोत
# हर स्रोत (url, शीर्षक/टैग) की सूची देता है, ताकि मेल जाँचा जा सके।
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
        out = []
        for h in r.json().get("hits", []):
            url = h.get("largeImageURL") or h.get("webformatURL")
            out.append((url, h.get("tags", "")))
        return out
    except Exception:
        return []


def web_urls(query):
    if DDGS is None:
        return []
    try:
        with DDGS() as d:
            results = list(d.images(query, max_results=25))
        return [(r.get("image"), r.get("title", "")) for r in results]
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
        out = []
        for item in r.json().get("results", []):
            tags = " ".join(t.get("name", "") for t in item.get("tags", []) or [])
            text = f"{item.get('title', '')} {tags}"
            out.append((item.get("url"), text))
            out.append((item.get("thumbnail"), text))
        return out
    except Exception:
        return []


def try_download(cands, save_path, used, words, max_tries=8):
    scored = []
    for url, text in cands:
        if not url or url in used:
            continue
        s = score_text(text, words) if words else 0
        if words and s < 1:
            continue  # शीर्षक/टैग में कुछ भी मेल नहीं खाता, छोड़ दें
        scored.append((s, url))

    scored.sort(key=lambda x: -x[0])

    tries = 0
    for _, url in scored:
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


def find_photo(queries, save_path, used, key, web_on, anchor):
    for q in queries:
        words = match_words(q, anchor)
        sources = []
        if key:
            sources.append(("Pixabay", lambda q=q: pixabay_urls(q, key)))
        if web_on:
            sources.append(("इंटरनेट", lambda q=q: web_urls(q)))
        sources.append(("Openverse", lambda q=q: openverse_urls(q)))

        for name, fetch in sources:
            if try_download(fetch(), save_path, used, words):
                return name, q
    return None, None


def ai_image(prompt, save_path, size):
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

        # 2) विषय और हर दृश्य की तस्वीर-योजना
        status.info("कहानी का विषय और हर दृश्य की योजना बन रही है...")
        topic = extra_topic.strip() or translate_hi_en(script, 600) or script[:200]

        anchor = extra_topic.strip()
        plans = [None] * total_scenes

        if gemini_key.strip():
            c_anchor, plans, err = plan_with_ai(
                scenes, script, gemini_key.strip()
            )
            if not any(plans):
                st.warning(
                    "Gemini से योजना नहीं बन सकी, सामान्य तरीका इस्तेमाल होगा। "
                    f"कारण: {err}"
                )
            anchor = anchor or c_anchor

        if not anchor:
            kws = english_keywords(topic)
            anchor = kws[0] if kws else ""

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
                shutil.copy(uploads[i], path)
                images.append(path)
                last_good = path
                source = "आपकी तस्वीर"
            else:
                plan = plans[i]
                english = ""

                if plan and plan.get("query"):
                    q = str(plan["query"]).strip()
                    queries = [with_anchor(q, anchor)]
                    if anchor:
                        queries.append(anchor)
                else:
                    queries, english = scene_queries(scene, topic, anchor)

                source, query = find_photo(
                    queries, path, used_urls, pixabay_key.strip(), use_web, anchor
                )

                if not source and use_ai:
                    status.info(
                        f"मेल खाती तस्वीर नहीं मिली, AI से बना रहे हैं: दृश्य {i + 1}"
                    )
                    if plan and plan.get("ai"):
                        prompt = str(plan["ai"])
                    else:
                        english = english or translate_hi_en(scene)
                        prompt = f"{anchor}, {english}"[:500]
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
                f"इन दृश्यों की मेल खाती तस्वीर नहीं मिली: {missing}। "
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
