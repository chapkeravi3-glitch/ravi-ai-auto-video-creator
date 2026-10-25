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
import hashlib
from urllib.parse import quote
from PIL import Image, ImageFont
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
#
# packages.txt (हिंदी कैप्शन के फ़ॉन्ट के लिए, Streamlit Cloud पर):
# fonts-noto-core

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

TRANSITIONS = [
    "fade", "slideleft", "wipeleft", "circleopen", "dissolve",
    "smoothright", "slideup", "radial", "zoomin", "slidedown",
    "wiperight", "fadewhite",
]

FPS = 24
LEAD = 0.25    # नई तस्वीर आने के बाद आवाज़ शुरू होने में देरी
PAD = 0.35     # दृश्य के अंत में ठहराव
TRANS = 0.5    # दृश्यों के बीच ट्रांज़िशन की अवधि
GEMINI_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3-flash-preview"]
HEADERS = {"User-Agent": "RaviAIVideoCreator/5.0 (personal video project)"}

CAPTION_SIZES = {"छोटा": 0.050, "मध्यम": 0.066, "बड़ा": 0.082}

FONT_PATHS = [
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Bold.ttf",
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]
FONT_URLS = [
    "https://github.com/google/fonts/raw/main/ofl/hind/Hind-Bold.ttf",
    "https://github.com/google/fonts/raw/main/ofl/mukta/Mukta-Bold.ttf",
]



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
    era = ""
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
            '- "exclude": a list of 0 to 4 English words that must NOT appear in '
            'the photo title (for example "test" when the story is about one-day '
            "matches, or wrong teams).\n"
            'Also return "anchor": the one or two word main subject of the whole story, '
            'and "era": "modern" if the story is about events after 2012, '
            'otherwise "historical".\n'
            "Respond with ONLY JSON, no markdown: "
            '{"anchor":"...","era":"modern","scenes":[{"n":1,"query":"...",'
            '"ai":"...","exclude":["..."]}]}'
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
        era = era or str(data.get("era", "")).strip().lower()
        for item in data.get("scenes", []):
            try:
                n = int(item.get("n")) - 1
            except Exception:
                continue
            if 0 <= n < len(scenes):
                plans[n] = item

    return anchor, plans, last_error, era


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


def visual_prompt(scene, topic, anchor, english=""):
    """दृश्य की कथा से एक ठोस English तस्वीर-विवरण बनाता है (AI तस्वीर के लिए)।"""
    ask = (
        "Write ONE English sentence (max 35 words) describing a single realistic "
        "photograph that illustrates this narration. Be concrete and visual: "
        "place, objects, setting, lighting. "
        f"Story topic: {topic[:200]}. "
        f"Narration (Hindi): {scene[:400]}. "
        "Reply with only the sentence."
    )
    try:
        r = requests.get(
            "https://text.pollinations.ai/" + quote(ask),
            params={"model": "openai"},
            headers=HEADERS,
            timeout=40,
        )
        if r.status_code == 200:
            t = r.text.strip().split("\n")[0].strip(" \"'")
            if 15 <= len(t) <= 300 and "{" not in t:
                return with_anchor(t, anchor)
    except Exception:
        pass

    english = english or translate_hi_en(scene)
    if english.strip():
        return with_anchor(english.strip()[:300], anchor)
    return ""  # विवरण नहीं बना तो बेमतलब तस्वीर नहीं बनाएँगे


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


OLD_YEAR = re.compile(r"\b(18\d\d|19\d\d|200\d|201[0-2])\b")


def try_download(cands, save_path, used, words, max_tries=8,
                 exclude=(), modern=False):
    need = 2 if len(words) >= 3 else 1
    ex = [e.lower() for e in exclude if e]
    scored = []
    for url, text in cands:
        if not url or url in used:
            continue
        t = (text or "").lower()
        if any(e in t for e in ex):
            continue
        if modern and OLD_YEAR.search(t):
            continue
        sc = score_text(text, words) if words else 0
        if words and sc < need:
            continue  # शीर्षक/टैग में पर्याप्त मेल नहीं
        scored.append((sc, url))

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


def find_photo(queries, save_path, used, key, web_on, anchor,
               exclude=(), modern=False):
    for q in queries:
        words = match_words(q, anchor)
        sources = []
        if key:
            sources.append(("Pixabay", lambda q=q: pixabay_urls(q, key)))
        if web_on:
            sources.append(("इंटरनेट", lambda q=q: web_urls(q)))
        sources.append(("Openverse", lambda q=q: openverse_urls(q)))

        for name, fetch in sources:
            if try_download(
                fetch(), save_path, used, words,
                exclude=exclude, modern=modern,
            ):
                return name, q
    return None, None


def guess_modern(*texts):
    blob = " ".join(texts).lower()
    if re.search(r"\b(20[1-3]\d)\b", blob):
        return True
    return bool(re.search(
        r"cricket|odi\b|t20|ipl|world cup|match|tournament|league|"
        r"election|smartphone|startup",
        blob,
    ))


def ai_image(prompt, save_path, size):
    if len(prompt.strip()) < 15:
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
                    "model": "flux",
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
def voice_params(text, speed_pct, natural):
    """हर दृश्य के लिए थोड़ा अलग गति और सुर, ताकि आवाज़ मशीनी न लगे।"""
    rate = int(speed_pct)
    pitch = 0
    if natural:
        rate += random.randint(-4, 3)
        pitch += random.randint(-3, 3)
        t = text.strip()
        if t.endswith("?"):
            pitch += 3
            rate += 2
        elif t.endswith("!"):
            pitch += 2
            rate += 3
    return f"{rate:+d}%", f"{pitch:+d}Hz"


async def synth(text, voice, path, rate, pitch):
    bounds = []
    try:
        comm = edge_tts.Communicate(
            text=text, voice=voice, rate=rate, pitch=pitch,
            boundary="WordBoundary",
        )
    except TypeError:
        comm = edge_tts.Communicate(
            text=text, voice=voice, rate=rate, pitch=pitch
        )

    with open(path, "wb") as f:
        async for chunk in comm.stream():
            kind = chunk.get("type")
            if kind == "audio":
                f.write(chunk["data"])
            elif kind in ("WordBoundary", "SentenceBoundary"):
                bounds.append((
                    chunk["offset"] / 1e7,
                    chunk["duration"] / 1e7,
                    chunk.get("text", ""),
                ))
    return bounds


async def make_voices(scenes, voice, folder, speed, natural, on_progress):
    results = []
    for i, text in enumerate(scenes):
        path = os.path.join(folder, f"voice_{i}.mp3")
        rate, pitch = voice_params(text, speed, natural)
        bounds = []
        for attempt in range(3):
            try:
                bounds = await synth(text, voice, path, rate, pitch)
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(2)
        results.append((path, bounds))
        on_progress(i)
    return results


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


# ---------------------------------------------------------------- कैप्शन
def build_cues(text, bounds, voice_len, max_chars=26, max_words=5):
    """बोले जा रहे शब्दों के हिसाब से छोटे-छोटे कैप्शन (शुरू, अंत, टेक्स्ट)।"""
    units = []
    for off, dur, t in bounds or []:
        t = (t or "").strip()
        if t:
            units.append((off, off + max(dur, 0.05), t))
    if not units:
        units = [(0.0, voice_len, text.strip())]

    words = []
    for s, e, t in units:
        ws = t.split()
        if not ws:
            continue
        total = sum(len(w) + 1 for w in ws)
        cur = s
        for w in ws:
            d = (e - s) * (len(w) + 1) / total
            words.append((cur, cur + d, w))
            cur += d

    cues, cur = [], []
    for w in words:
        cand = " ".join(x[2] for x in cur + [w])
        if cur and (len(cand) > max_chars or len(cur) >= max_words):
            cues.append([cur[0][0], cur[-1][1], " ".join(x[2] for x in cur)])
            cur = [w]
        else:
            cur.append(w)
    if cur:
        cues.append([cur[0][0], cur[-1][1], " ".join(x[2] for x in cur)])

    if len(cues) >= 2 and len(cues[-1][2].split()) == 1:
        if len(cues[-2][2]) + len(cues[-1][2]) <= max_chars + 12:
            cues[-2][1] = cues[-1][1]
            cues[-2][2] += " " + cues[-1][2]
            cues.pop()

    for i in range(len(cues) - 1):
        cues[i][1] = cues[i + 1][0]
    if cues:
        cues[-1][1] = max(cues[-1][1], voice_len) + 0.15
    return [tuple(c) for c in cues]


def get_caption_font():
    for p in FONT_PATHS:
        if os.path.exists(p):
            return p

    d = os.path.join(tempfile.gettempdir(), "ravi_fonts")
    os.makedirs(d, exist_ok=True)
    for url in FONT_URLS:
        p = os.path.join(d, os.path.basename(url))
        if os.path.exists(p) and os.path.getsize(p) > 10000:
            return p
        try:
            r = requests.get(url, headers=HEADERS, timeout=40)
            r.raise_for_status()
            if len(r.content) > 10000:
                with open(p, "wb") as f:
                    f.write(r.content)
                return p
        except Exception:
            continue
    return None


def font_info(path):
    try:
        family, style = ImageFont.truetype(path, 20).getname()
        return family, ("bold" in style.lower())
    except Exception:
        return None, False


def ass_time(t):
    cs = int(round(max(0.0, t) * 100))
    hh = cs // 360000
    mm = (cs // 6000) % 60
    ss = (cs // 100) % 60
    return f"{hh}:{mm:02d}:{ss:02d}.{cs % 100:02d}"


def write_ass(path, cues, size, font_path, size_ratio, lead):
    w, h = size
    family, is_bold = font_info(font_path)
    if not family:
        return False

    fontsize = int(min(w, h) * size_ratio * (1.0 if w <= h else 0.95))
    outline = max(2, fontsize // 14)
    margin_v = int(h * (0.14 if h > w else 0.09))
    margin_lr = int(w * 0.07)

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {w}",
        f"PlayResY: {h}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{family},{fontsize},&H00FFFFFF,&H000000FF,"
        f"&H00000000,&H80000000,{0 if is_bold else -1},0,0,0,100,100,0,0,1,"
        f"{outline},2,2,{margin_lr},{margin_lr},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text",
    ]

    for start, end, text in cues:
        clean = text.replace("\\", "").replace("{", "(").replace("}", ")")
        pop = r"{\fad(100,60)\fscx88\fscy88\t(0,140,\fscx100\fscy100)}"
        lines.append(
            f"Dialogue: 0,{ass_time(start + lead)},{ass_time(end + lead)},"
            f"Default,,0,0,0,,{pop}{clean}"
        )

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return True


# ---------------------------------------------------------------- वीडियो क्लिप
def motion_filter(effect, frames, w, h):
    cx = "iw/2-(iw/zoom/2)"
    cy = "ih/2-(ih/zoom/2)"

    if effect == "Zoom Out":
        z, x, y = f"1.14-0.14*on/{frames}", cx, cy
    elif effect == "Pan Left to Right":
        z = "1.14"
        x = f"(iw-iw/zoom)*on/{frames}"
        y = "(ih-ih/zoom)/2"
    elif effect == "Pan Right to Left":
        z = "1.14"
        x = f"(iw-iw/zoom)*(1-on/{frames})"
        y = "(ih-ih/zoom)/2"
    else:  # Zoom In
        z, x, y = f"1+0.14*on/{frames}", cx, cy

    return (
        f"zoompan=z='{z}':x='{x}':y='{y}':"
        f"d={frames}:s={w}x{h}:fps={FPS}"
    )


def end_state_filter(effect, w, h):
    """पिछले क्लिप की आख़िरी झलक (ताकि ट्रांज़िशन में झटका न लगे)।"""
    cx = "iw/2-(iw/zoom/2)"
    cy = "ih/2-(ih/zoom/2)"

    if effect == "Zoom Out":
        z, x, y = "1.0", "0", "0"
    elif effect == "Pan Left to Right":
        z, x, y = "1.14", "iw-iw/zoom", "(ih-ih/zoom)/2"
    elif effect == "Pan Right to Left":
        z, x, y = "1.14", "0", "(ih-ih/zoom)/2"
    else:
        z, x, y = "1.14", cx, cy

    return (
        f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={w}x{h}:fps={FPS}"
    )


def compose(src, k, ww, hh):
    """धुंधली पृष्ठभूमि + बीच में पूरी तस्वीर (CapCut/InShot जैसा)।"""
    return (
        f"[{src}]split[a{k}][b{k}];"
        f"[a{k}]scale={ww // 4}:{hh // 4}:force_original_aspect_ratio=increase,"
        f"crop={ww // 4}:{hh // 4},boxblur=6:2,scale={ww}:{hh}[bg{k}];"
        f"[b{k}]scale={ww}:{hh}:force_original_aspect_ratio=decrease[fg{k}];"
        f"[bg{k}][fg{k}]overlay=(W-w)/2:(H-h)/2[c{k}]"
    )


def make_clip(image, prev, prev_effect, audio, voice_len, cues, out, size,
              effect, transition, ffmpeg, font, size_ratio, folder,
              pad, is_last):
    w, h = size
    total = LEAD + voice_len + pad
    frames = max(1, math.ceil(total * FPS))
    bw, bh = w * 3 // 2, h * 3 // 2

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", image]
    graph = [
        compose("0:v", "0", bw, bh),
        f"[c0]{motion_filter(effect, frames, w, h)},"
        f"fps={FPS},setsar=1,format=yuv420p[cur]",
    ]

    if prev:
        cmd += [
            "-loop", "1", "-framerate", str(FPS),
            "-t", f"{TRANS + 0.3:.2f}", "-i", prev,
        ]
        graph += [
            compose("1:v", "1", bw, bh),
            f"[c1]{end_state_filter(prev_effect, w, h)},"
            f"fps={FPS},setsar=1,format=yuv420p[prv]",
            f"[prv][cur]xfade=transition={transition}:"
            f"duration={TRANS}:offset=0[x]",
        ]
        base, audio_idx = "x", 2
    else:
        base, audio_idx = "cur", 1

    cmd += ["-i", audio]

    post = f"[{base}]eq=contrast=1.05:saturation=1.12,vignette"

    if cues and font:
        ass_path = os.path.join(
            folder, f"cap_{hashlib.md5(out.encode()).hexdigest()[:8]}.ass"
        )
        if write_ass(ass_path, cues, size, font, size_ratio, LEAD):
            fdir = os.path.dirname(font)
            post += f",ass=filename='{ass_path}':fontsdir='{fdir}'"

    if not prev:
        post += f",fade=t=in:st=0:d={TRANS}"
    if is_last:
        post += f",fade=t=out:st={max(0.0, total - 0.6):.3f}:d=0.6"

    post += ",setsar=1,format=yuv420p[v]"
    graph.append(post)

    cmd += [
        "-filter_complex", ";".join(graph),
        "-map", "[v]",
        "-map", f"{audio_idx}:a",
        "-af",
        "aresample=44100,aformat=channel_layouts=stereo,"
        f"adelay={int(LEAD * 1000)}|{int(LEAD * 1000)},apad=pad_dur={pad}",
        "-t", f"{total:.3f}",
        "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
        out,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2500:])


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
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2500:])


def add_music(video, music, out, volume, ffmpeg):
    fc = (
        "[0:a]volume=1.0[a0];"
        f"[1:a]volume={volume}[a1];"
        "[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[mix];"
        "[mix]volume=2.0[a]"
    )
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", video,
        "-stream_loop", "-1", "-i", music,
        "-filter_complex", fc,
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
        "-shortest",
        "-movflags", "+faststart",
        out,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2500:])


# ==== UI शुरू (परीक्षण में यहाँ से आगे का हिस्सा नहीं चलाया जाता) ====

import threading


# ---------------------------------------------------------------- बैकग्राउंड काम
# काम एक अलग thread में चलता है और उसकी स्थिति सर्वर की याददाश्त में रहती है।
# इसलिए फ़ोन की स्क्रीन बंद होने या ब्राउज़र छूटने पर भी वीडियो बनता रहता है।
def save_project(P):
    """स्थिति डिस्क पर रखें, ताकि ऐप रुकने के बाद भी काम लौटाया जा सके।"""
    try:
        data = {k: v for k, v in P.items() if k != "used"}
        data["used"] = list(P.get("used", []))
        path = os.path.join(P["folder"], "project.json")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=list)
        os.replace(path + ".tmp", path)
    except Exception:
        pass


def load_saved_projects(jobs):
    base = tempfile.gettempdir()
    try:
        names = os.listdir(base)
    except Exception:
        return
    for name in names:
        if not name.startswith("ravi_video_"):
            continue
        path = os.path.join(base, name, "project.json")
        try:
            if time.time() - os.path.getmtime(path) > 24 * 3600:
                continue
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data["used"] = set(data.get("used", []))
            data["size"] = tuple(data.get("size") or (720, 1280))
            for sc in data.get("scenes", []):
                sc["cues"] = [tuple(c) for c in sc.get("cues", [])]
            if data.get("state") in ("drafting", "rendering"):
                if data.get("draft_done"):
                    data["state"] = "ready"
                    data["note"] = (
                        "पिछली बार वीडियो बनते-बनते ऐप रुक गया था। "
                        "'वीडियो बनाएँ' दबाएँ। जो क्लिप बन चुके हैं वे दोबारा नहीं बनेंगे।"
                    )
                elif data.get("scenes"):
                    data["state"] = "error"
                    data["error"] = "ड्राफ़्ट बनते समय ऐप रुक गया था।"
                else:
                    continue
            jobs[data["id"]] = data
        except Exception:
            continue


@st.cache_resource
def get_jobs():
    jobs = {}
    load_saved_projects(jobs)
    return jobs


JOBS = get_jobs()

for _id, _p in list(JOBS.items()):
    if (
        time.time() - _p.get("created", 0) > 6 * 3600
        and _p["state"] not in ("drafting", "rendering")
    ):
        shutil.rmtree(_p.get("folder", ""), ignore_errors=True)
        JOBS.pop(_id, None)


def set_state(P, status=None, progress=None):
    if status is not None:
        P["status"] = status
    if progress is not None:
        P["progress"] = int(progress)


def draft_job(P, cfg):
    """आवाज़, तस्वीर-योजना और तस्वीरें तैयार करता है। रुकने पर वहीं से आगे बढ़ सकता है।"""
    try:
        folder = P["folder"]
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        size = cfg["size"]
        resume = bool(cfg.get("resume")) and bool(P.get("scenes"))

        P["state"] = "drafting"
        P["error"] = ""

        # 1) आवाज़ (बन चुकी हो तो दोबारा नहीं बनती)
        if not resume:
            texts = split_scenes(P["script"], cfg["max_chars"])
            n = len(texts)
            set_state(P, "हिंदी आवाज़ बन रही है...", 1)
            voices = asyncio.run(
                make_voices(
                    texts, cfg["voice"], folder, cfg["speed"], cfg["natural"],
                    lambda i: set_state(P, None, 2 + (i + 1) / n * 23),
                )
            )
            scenes = []
            for text, (vpath, bounds) in zip(texts, voices):
                vlen = audio_duration(vpath, ffmpeg)
                scenes.append({
                    "text": text,
                    "voice_path": vpath,
                    "voice_len": vlen,
                    "cues": build_cues(text, bounds, vlen),
                    "pad": round(random.uniform(0.3, 0.55), 2)
                    if cfg["natural"] else 0.35,
                    "image": None,
                    "source": "",
                    "query": "",
                    "effect": "Default",
                    "transition": "Default",
                    "keep": True,
                })
            P["scenes"] = scenes
            P["offset"] = random.randint(0, 11)
            save_project(P)
        else:
            scenes = P["scenes"]
            n = len(scenes)

        texts = [sc["text"] for sc in scenes]

        # 2) विषय और योजना (बनी हुई हो तो वही)
        extra = cfg["extra_topic"].strip()
        if not P.get("planned"):
            set_state(P, "कहानी का विषय और हर दृश्य की योजना बन रही है...", 26)
            topic = extra or translate_hi_en(P["script"], 600) or P["script"][:200]
            anchor = extra
            plans = [None] * n
            era = ""
            if cfg["gemini_key"]:
                g_anchor, plans, err, era = plan_with_ai(
                    texts, P["script"], cfg["gemini_key"]
                )
                if not any(plans):
                    P["note"] = (
                        "Gemini से योजना नहीं बन सकी, सामान्य तरीका इस्तेमाल हुआ। "
                        f"कारण: {err}"
                    )
                anchor = anchor or g_anchor
            if not anchor:
                kws = english_keywords(topic)
                anchor = kws[0] if kws else ""

            modern = era.startswith("modern") if era else guess_modern(
                topic, P["script"], extra
            )
            P.update(anchor=anchor, topic=topic, modern=modern,
                     plans=plans, planned=True)
            save_project(P)

        anchor, topic, modern = P["anchor"], P["topic"], P["modern"]
        plans = P.get("plans") or [None] * n

        # 3) तस्वीरें (जिनकी बन चुकी हैं उन्हें छोड़ देता है)
        used = P["used"]
        last_good = None
        uploads = cfg["uploads"]

        for i, sc in enumerate(scenes):
            if sc.get("image") and os.path.exists(sc["image"]):
                last_good = sc["image"]
                continue

            set_state(P, f"तस्वीर खोज रहे हैं: दृश्य {i + 1}/{n}",
                      27 + (i + 1) / n * 70)
            path = os.path.join(folder, f"scene_{i}_0.jpg")

            if i < len(uploads):
                shutil.copy(uploads[i], path)
                sc.update(image=path, source="आपकी तस्वीर", query="")
                last_good = path
                save_project(P)
                continue

            plan = plans[i] if i < len(plans) else None
            english = ""
            exclude = []
            if plan and plan.get("query"):
                q = str(plan["query"]).strip()
                queries = [with_anchor(q, anchor)]
                if anchor:
                    queries.append(anchor)
                ex = plan.get("exclude") or []
                exclude = [str(e) for e in ex] if isinstance(ex, list) else []
            else:
                queries, english = scene_queries(sc["text"], topic, anchor)

            source, query = find_photo(
                queries, path, used, cfg["pixabay_key"], cfg["use_web"],
                anchor, exclude=exclude, modern=modern,
            )

            if not source and cfg["use_ai"]:
                set_state(P, f"मेल खाती तस्वीर नहीं मिली, AI से बना रहे हैं: दृश्य {i + 1}")
                if plan and plan.get("ai"):
                    prompt = str(plan["ai"])
                else:
                    prompt = visual_prompt(sc["text"], topic, anchor, english)
                if prompt and ai_image(prompt, path, size):
                    source, query = "AI", prompt[:120]

            if source:
                sc.update(image=path, source=source, query=query)
                last_good = path
            else:
                make_fallback_image(path, size, last_good)
                sc.update(image=path, source="खाली", query="")
                P.setdefault("missing", []).append(i + 1)

            save_project(P)

        if P.get("missing"):
            P["note"] = (P.get("note", "") +
                         f" इन दृश्यों की मेल खाती तस्वीर नहीं मिली: {sorted(set(P['missing']))}। "
                         "एडिटर में इनकी तस्वीर बदलें।").strip()

        P["draft_done"] = True
        set_state(P, "ड्राफ़्ट तैयार है", 100)
        P["state"] = "rendering" if cfg.get("chain") else "ready"
        save_project(P)

    except Exception as e:
        P["state"] = "error"
        P["error"] = str(e)
        save_project(P)


def render_job(P, cfg):
    """चुनी हुई तस्वीरों, इफेक्ट और ट्रांज़िशन से अंतिम वीडियो बनाता है।"""
    try:
        folder = P["folder"]
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        size = cfg["size"]
        P["error"] = ""
        kept = [dict(s) for s in P["scenes"] if s["keep"]]
        n = len(kept)
        if n == 0:
            raise RuntimeError("कम से कम एक दृश्य रखना ज़रूरी है।")

        font = None
        if cfg["captions"]:
            font = get_caption_font()
            if not font:
                P["note"] = "कैप्शन के लिए हिंदी फ़ॉन्ट नहीं मिला, इसलिए वीडियो बिना कैप्शन के बना।"

        off = P.get("offset", 0)

        # हर दृश्य का इफेक्ट और ट्रांज़िशन तय करें
        effects, trans = [], []
        for idx, sc in enumerate(kept):
            e = sc["effect"] if sc["effect"] != "Default" else cfg["default_effect"]
            if e.startswith("Auto"):
                e = EFFECTS[(idx + off) % len(EFFECTS)]
            t = sc["transition"] if sc["transition"] != "Default" else cfg["default_transition"]
            if t.startswith("Auto"):
                t = TRANSITIONS[(idx * 5 + off) % len(TRANSITIONS)]
            effects.append(e)
            trans.append(t)

        clips = []
        for idx, sc in enumerate(kept):
            set_state(P, f"क्लिप बन रहा है: {idx + 1}/{n}", 2 + idx / n * 88)
            prev = kept[idx - 1]["image"] if idx > 0 else None
            prev_eff = effects[idx - 1] if idx > 0 else None

            sig = repr((
                sc["image"], os.path.getmtime(sc["image"]),
                prev, prev_eff, effects[idx], trans[idx] if prev else "",
                sc["voice_path"], os.path.getmtime(sc["voice_path"]),
                sc["cues"] if font else None, font, size,
                cfg["cap_ratio"], sc["pad"], idx == n - 1,
            ))
            key = hashlib.md5(sig.encode()).hexdigest()[:16]
            clip = os.path.join(folder, f"clip_{key}.mp4")

            if os.path.exists(clip):
                set_state(P, f"क्लिप {idx + 1}/{n}: पहले से बना हुआ मिला")
            else:
                tmp = clip + ".part.mp4"
                make_clip(
                    sc["image"], prev, prev_eff, sc["voice_path"],
                    sc["voice_len"], sc["cues"] if font else None, tmp,
                    size, effects[idx], trans[idx], ffmpeg, font,
                    cfg["cap_ratio"], folder, sc["pad"], idx == n - 1,
                )
                os.replace(tmp, clip)
            clips.append(clip)

        set_state(P, "पूरा वीडियो जोड़ा जा रहा है...", 92)
        stamp = int(time.time())
        joined = os.path.join(folder, f"video_{stamp}.mp4")
        join_clips(clips, folder, joined, ffmpeg)

        final = joined
        if cfg.get("music"):
            set_state(P, "पृष्ठभूमि संगीत जोड़ा जा रहा है...", 96)
            final = os.path.join(folder, f"video_{stamp}_music.mp4")
            add_music(joined, cfg["music"], final, cfg["music_vol"], ffmpeg)

        P["video"] = final
        set_state(P, "वीडियो तैयार है!", 100)
        P["state"] = "done"
        save_project(P)

    except Exception as e:
        P["state"] = "error"
        P["error"] = str(e)


def full_job(P, cfg):
    """कहानी से तैयार वीडियो तक पूरा काम (रुकने पर वहीं से आगे बढ़ता है)।"""
    if not P.get("draft_done"):
        draft_job(P, dict(cfg, chain=True))
        if not P.get("draft_done"):
            return
    P["state"] = "rendering"
    P["progress"] = 0
    render_job(P, cfg["render"])


def start_thread(fn, P, cfg):
    t = threading.Thread(target=fn, args=(P, cfg), daemon=True)
    t.start()



# ==== UI ====
st.title("🎬 Ravi AI Auto Video Creator")
st.write("सिर्फ़ हिंदी कहानी डालें। कहानी के हिसाब से तस्वीरें चुनकर एनिमेशन वाला वीडियो बनेगा।")

pid = st.query_params.get("p")
P = JOBS.get(pid) if pid else None
if P is None and JOBS:
    # लिंक में पहचान न हो (जैसे ऐप दोबारा खोला) तो आख़िरी प्रोजेक्ट खोलें
    P = max(JOBS.values(), key=lambda p: p.get("created", 0))
    st.query_params["p"] = P["id"]

busy = bool(P) and P["state"] in ("drafting", "rendering")

# ---------------------------------------------------------------- इनपुट
script = st.text_area("📝 पूरी हिंदी कहानी लिखें", height=280)

col1, col2 = st.columns(2)
with col1:
    voice_name = st.selectbox("🎙️ आवाज़", list(VOICES.keys()))
    speed = st.slider("बोलने की गति", -25, 15, -5, 1, help="ऋण का मतलब थोड़ा धीरे")
    format_name = st.selectbox("📐 वीडियो का आकार", list(FORMATS.keys()))
with col2:
    effect_choice = st.selectbox(
        "🎞️ फोटो इफेक्ट", ["Auto (हर दृश्य में अलग)"] + EFFECTS
    )
    max_chars = st.slider(
        "✂️ एक दृश्य में अधिकतम अक्षर (कम = तस्वीरें जल्दी-जल्दी बदलेंगी)",
        60, 400, 140, 10,
    )
    cap_name = st.select_slider(
        "कैप्शन का आकार", list(CAPTION_SIZES.keys()), value="मध्यम"
    )

natural = st.checkbox(
    "🗣️ प्राकृतिक आवाज़ (हर दृश्य में थोड़ी अलग गति/सुर, प्रश्न और विस्मय पर बदलाव)",
    value=True,
)
captions_on = st.checkbox("💬 हिंदी कैप्शन (बोले जा रहे शब्द स्क्रीन पर)", value=True)
use_web = st.checkbox(
    "🌐 इंटरनेट की तस्वीरों में खोजें (कॉपीराइट आपकी ज़िम्मेदारी है)", value=True
)
use_ai = st.checkbox(
    "🤖 मेल खाती तस्वीर न मिले तो AI से बनाएँ (बेमेल तस्वीर लगाने से यह बेहतर है)",
    value=True,
)

gemini_key = st.text_input(
    "Google Gemini API key (मुफ़्त; aistudio.google.com से; सबसे सटीक तस्वीरों के लिए)",
    type="password",
)

with st.expander("और विकल्प (वैकल्पिक)"):
    extra_topic = st.text_input(
        "कहानी का मुख्य विषय English में (हर खोज में जुड़ेगा)",
        placeholder="India vs Australia ODI 2026",
    )
    pixabay_key = st.text_input("Pixabay API key (मुफ़्त)", type="password")

uploaded_images = st.file_uploader(
    "अपनी तस्वीरें भी लगा सकते हैं (पहले दृश्यों में क्रम से लगेंगी)",
    type=["jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True,
)


def make_cfg(size, resume=False, uploads=None):
    return {
        "resume": resume,
        "voice": VOICES[voice_name], "speed": speed, "natural": natural,
        "max_chars": max_chars, "size": size,
        "use_web": use_web, "use_ai": use_ai,
        "gemini_key": gemini_key.strip(), "pixabay_key": pixabay_key.strip(),
        "extra_topic": extra_topic, "uploads": uploads or [],
        "render": {
            "size": size, "default_effect": effect_choice,
            "default_transition": "Auto (हर दृश्य अलग)",
            "captions": captions_on, "cap_ratio": CAPTION_SIZES[cap_name],
            "music": None, "music_vol": 0.1,
        },
    }


start = st.button("🎬 वीडियो बनाना शुरू करें", type="primary", disabled=busy)

if start:
    if not script.strip():
        st.error("पहले कहानी लिखें।")
        st.stop()

    if P and os.path.exists(P.get("folder", "")):
        shutil.rmtree(P["folder"], ignore_errors=True)
        JOBS.pop(P["id"], None)

    new_id = hashlib.md5(f"{time.time()}{random.random()}".encode()).hexdigest()[:10]
    folder = tempfile.mkdtemp(prefix="ravi_video_")

    uploads = []
    for i, up in enumerate(uploaded_images or []):
        p = os.path.join(folder, f"upload_{i}.jpg")
        with Image.open(up) as img:
            img.convert("RGB").save(p, "JPEG", quality=90)
        uploads.append(p)

    size = FORMATS[format_name]
    P = {
        "id": new_id, "folder": folder, "state": "drafting",
        "created": time.time(), "script": script, "draft_done": False,
        "planned": False, "status": "शुरू हो रहा है...", "progress": 0,
        "note": "", "error": "", "scenes": [], "used": set(), "video": None,
        "anchor": "", "topic": "", "modern": False, "offset": 0,
        "voice": VOICES[voice_name], "speed": speed, "natural": natural,
        "size": size,
    }
    JOBS[new_id] = P
    start_thread(full_job, P, make_cfg(size, uploads=uploads))
    st.query_params["p"] = new_id
    st.rerun()

# ---------------------------------------------------------------- स्थिति
if P is None:
    st.stop()

if P["state"] in ("drafting", "rendering"):
    st.progress(min(100, P["progress"]))
    st.info(P["status"])
    st.caption(
        "💡 काम सर्वर पर चलता है। फ़ोन की स्क्रीन बंद हो जाए तो भी चलता रहेगा। "
        "बाद में ऐप दोबारा खोलने पर यहीं से आगे दिखेगा। "
        "रुक भी जाए तो बना हुआ काम दोबारा नहीं बनेगा।"
    )
    time.sleep(3)
    st.rerun()

video_ready = bool(P.get("video")) and os.path.exists(P["video"])

if P.get("note"):
    st.warning(P["note"])

if P["state"] == "error" and P.get("error"):
    st.error("बीच में समस्या आई।")
    st.code(P["error"][-600:])

if not video_ready:
    done_n = sum(1 for sc in P["scenes"] if sc.get("image"))
    if P["scenes"]:
        st.warning(
            f"वीडियो अभी पूरा नहीं बना (तस्वीरें {done_n}/{len(P['scenes'])})। "
            "जो बन चुका है वह दोबारा नहीं बनेगा।"
        )
    if st.button("▶️ वहीं से आगे बढ़ाएँ", type="primary"):
        P["error"], P["note"] = "", ""
        P["state"] = "drafting" if not P.get("draft_done") else "rendering"
        start_thread(
            full_job, P, make_cfg(FORMATS[format_name], resume=True)
        )
        st.rerun()
    st.stop()

# ---------------------------------------------------------------- तैयार वीडियो
st.success("वीडियो तैयार है!")
st.video(P["video"])
with open(P["video"], "rb") as f:
    st.download_button(
        "⬇️ MP4 डाउनलोड करें", data=f,
        file_name="ravi_video.mp4", mime="video/mp4",
    )

with st.expander("किस दृश्य में कौन-सी तस्वीर लगी"):
    for i, sc in enumerate(P["scenes"]):
        if sc.get("image") and os.path.exists(sc["image"]):
            st.image(
                sc["image"], width=180,
                caption=f"दृश्य {i + 1} | {sc['source']} | {sc['query'][:80]}",
            )
