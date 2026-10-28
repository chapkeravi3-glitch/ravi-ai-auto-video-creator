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
from PIL import Image, ImageFont, ImageOps, ImageStat
import edge_tts
import imageio_ffmpeg

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

# requirements.txt:
# streamlit
# requests
# pillow
# edge-tts
# imageio-ffmpeg
# deep-translator
# ddgs
# pillow-heif
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

_APP_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else "."
LOGO_PATH = os.path.join(_APP_DIR, "logo.png")

st.set_page_config(
    page_title="Ravi Studio - AI वीडियो क्रिएटर",
    page_icon=LOGO_PATH if os.path.exists(LOGO_PATH) else "🎬",
    layout="centered",
    initial_sidebar_state="collapsed",
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
HEADERS = {"User-Agent": "RaviAIVideoCreator/6.0 (personal video project)"}
APP_VERSION = "Ravi Studio • संस्करण 7"

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


def gemini_models(key):
    """Google से बताए गए उपलब्ध flash मॉडल (नाम बदलते रहते हैं)।"""
    try:
        r = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"pageSize": 100},
            headers={"x-goog-api-key": key},
            timeout=30,
        )
        if r.status_code != 200:
            return [], f"{r.status_code}: {r.text[:200]}"
        names = []
        for m in r.json().get("models", []):
            n = m.get("name", "").replace("models/", "")
            low = n.lower()
            if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                continue
            if "flash" not in low:
                continue
            if any(x in low for x in (
                "image", "tts", "live", "audio", "embedding", "thinking",
                "robotics", "computer", "vision", "learnlm", "gemma",
            )):
                continue
            names.append(n)

        def rank(n):
            nums = re.findall(r"\d+(?:\.\d+)?", n)
            v = float(nums[0]) if nums else 0.0
            return (-v, "lite" in n.lower(), "preview" in n.lower() or "exp" in n.lower())

        names.sort(key=rank)
        return names[:4], ""
    except Exception as e:
        return [], str(e)[:200]


def gemini_generate(key, prompt, models):
    errors = []
    url = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
    for model in models:
        for use_json in (True, False):
            try:
                body = {"contents": [{"parts": [{"text": prompt}]}]}
                if use_json:
                    body["generationConfig"] = {"responseMimeType": "application/json"}
                r = requests.post(
                    url.format(model),
                    headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                    json=body,
                    timeout=90,
                )
                if r.status_code == 200:
                    cands = r.json().get("candidates") or []
                    if cands:
                        parts = cands[0].get("content", {}).get("parts", [])
                        text = "".join(p.get("text", "") for p in parts)
                        if text.strip():
                            return text, ""
                    errors.append(f"{model}: खाली जवाब")
                    break
                errors.append(f"{model} {r.status_code}: {r.text[:160]}")
                if r.status_code == 429:
                    time.sleep(5)
                if r.status_code == 400 and use_json and "mime" in r.text.lower():
                    continue  # JSON मोड नहीं चलता, बिना उसके दोबारा
                if r.status_code in (400, 401, 403) and "key" in r.text.lower():
                    return "", " | ".join(errors)[:500]  # key ही गलत है
                break
            except Exception as e:
                errors.append(f"{model}: {str(e)[:100]}")
                break
    return "", " | ".join(errors)[:500]


def test_gemini(key):
    models, list_err = gemini_models(key)
    if not models:
        if list_err:
            return False, f"key जाँच में समस्या: {list_err}"
        models = GEMINI_MODELS
    text, err = gemini_generate(key, 'Reply with the JSON {"ok": true}', models)
    if text:
        return True, f"key सही है ✅ (मॉडल: {models[0]})"
    return False, f"काम नहीं बना: {err}"


def plan_with_ai(scenes, opening, key, on_progress=None):
    """हर दृश्य के लिए तस्वीर की खोज और AI prompt, मुफ़्त Gemini से।"""
    anchor = ""
    era = ""
    plans = [None] * len(scenes)
    last_error = ""
    CH = 15
    models, list_err = gemini_models(key)
    if not models:
        models = GEMINI_MODELS
        last_error = list_err

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
            text, err = gemini_generate(key, prompt, models)
            if err:
                last_error = err
            data = parse_json(text) if text else None
            if data:
                break

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
def _note(err, msg):
    if err is not None:
        err.append(msg)


def pixabay_urls(query, key, err=None):
    lang = "hi" if re.search(r"[\u0900-\u097F]", query) else "en"
    try:
        r = requests.get(
            "https://pixabay.com/api/",
            params={
                "key": key, "q": query[:100], "lang": lang,
                "image_type": "photo", "per_page": 30, "safesearch": "true",
            },
            headers=HEADERS, timeout=25,
        )
        r.raise_for_status()
        out = []
        for h in r.json().get("hits", []):
            url = h.get("largeImageURL") or h.get("webformatURL")
            out.append((url, h.get("tags", "")))
        return out
    except Exception as e:
        _note(err, f"Pixabay: {str(e)[:90]}")
        return []


def web_urls(query, err=None):
    if DDGS is None:
        _note(err, "इंटरनेट खोज की लाइब्रेरी (ddgs) इंस्टॉल नहीं है")
        return []
    try:
        with DDGS() as d:
            results = list(d.images(query, max_results=25))
        return [(r.get("image"), r.get("title", "")) for r in results]
    except Exception as e:
        _note(err, f"इंटरनेट खोज: {type(e).__name__}: {str(e)[:90]}")
        return []


def openverse_urls(query, err=None):
    try:
        r = requests.get(
            "https://api.openverse.org/v1/images/",
            params={"q": query, "page_size": 20, "mature": "false"},
            headers=HEADERS, timeout=25,
        )
        r.raise_for_status()
        out = []
        for item in r.json().get("results", []):
            tags = " ".join(t.get("name", "") for t in item.get("tags", []) or [])
            text = f"{item.get('title', '')} {tags}"
            out.append((item.get("url"), text))
            out.append((item.get("thumbnail"), text))
        return out
    except Exception as e:
        _note(err, f"Openverse: {str(e)[:90]}")
        return []


def wikimedia_urls(query, err=None):
    """ऐतिहासिक विषयों (जगह, राजा, इमारत) के लिए बहुत अच्छा स्रोत।"""
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": query, "gsrnamespace": 6, "gsrlimit": 20,
        "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
        "iiurlwidth": 1600,
    }
    for attempt in range(2):
        try:
            r = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params=params, headers=HEADERS, timeout=25,
            )
            if r.status_code == 429:
                time.sleep(2 + attempt * 2)
                continue
            r.raise_for_status()
            pages = r.json().get("query", {}).get("pages", {})
            out = []
            for page in pages.values():
                info = (page.get("imageinfo") or [{}])[0]
                if info.get("mime") not in ("image/jpeg", "image/png"):
                    continue
                url = info.get("thumburl") or info.get("url")
                meta = info.get("extmetadata") or {}
                desc = re.sub(
                    r"<[^>]+>", " ",
                    (meta.get("ImageDescription") or {}).get("value", ""),
                )
                out.append((url, f"{page.get('title', '')} {desc}"))
            return out
        except Exception as e:
            _note(err, f"Wikimedia: {str(e)[:90]}")
    return []


OLD_YEAR = re.compile(r"\b(18\d\d|19\d\d|200\d|201[0-2])\b")


def to_rgb(img):
    """पारदर्शी हिस्से काले नहीं, सफ़ेद पृष्ठभूमि पर रखता है।"""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


def is_blank(img):
    """लगभग एक ही रंग की (काली/सफ़ेद/सादी) तस्वीर पहचानता है।"""
    stat = ImageStat.Stat(img.convert("L").resize((64, 64)))
    return stat.stddev[0] < 9 or stat.mean[0] < 12 or stat.mean[0] > 248


def try_download(cands, save_path, used, words, max_tries=8,
                 exclude=(), modern=False, need=None, stats=None):
    if need is None:
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
    if stats is not None:
        stats["pass"] = len(scored)

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
                rgb = to_rgb(img)
            if rgb.width < 500 or rgb.height < 350 or is_blank(rgb):
                continue
            rgb.thumbnail((2000, 2000))
            rgb.save(save_path, "JPEG", quality=90)
            used.add(url)
            return True
        except Exception:
            continue
    return False


def find_photo(queries, save_path, used, key, web_on, anchor,
               exclude=(), modern=False, relaxed=False, log=None):
    for q in queries:
        words = match_words("" if relaxed else q, anchor)
        sources = []
        if key:
            sources.append(("Pixabay", lambda e, q=q: pixabay_urls(q, key, e)))
        if not modern:
            sources.append(("Wikimedia", lambda e, q=q: wikimedia_urls(q, e)))
        if web_on:
            sources.append(("इंटरनेट", lambda e, q=q: web_urls(q, e)))
        sources.append(("Openverse", lambda e, q=q: openverse_urls(q, e)))

        for name, fetch in sources:
            errs, stats = [], {}
            cands = fetch(errs)
            got = try_download(
                cands, save_path, used, words,
                exclude=exclude, modern=modern,
                need=1 if relaxed else None, stats=stats,
            )
            if log is not None:
                line = (
                    f"'{q}' → {name}: {len(cands)} मिलीं, "
                    f"{stats.get('pass', 0)} मेल खाईं"
                    + (" ✅ ली गई" if got else "")
                )
                if errs:
                    line += f" | समस्या: {errs[0]}"
                log.append(line)
            if got:
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


_AI_GATE = {"t": 0.0}
AI_GAP = 16  # सेकंड: Pollinations की अनाम सेवा 15 सेकंड में एक अनुरोध ही लेती है


def ai_image(prompt, save_path, size, err=None):
    """Pollinations से तस्वीर बनवाता है (बिना key)। खाली/ख़राब तस्वीर नकारता है।"""
    if len(prompt.strip()) < 15:
        return False

    w, h = size
    scale = 1024 / max(w, h)
    w = max(256, int(w * scale) // 8 * 8)
    h = max(256, int(h * scale) // 8 * 8)
    full = (
        f"{prompt}, realistic photograph, natural lighting, "
        "high detail, no text, no watermark"
    )

    started = time.time()
    for model in ("flux", "flux", None):
        if time.time() - started > 300:
            return False

        wait = AI_GAP - (time.time() - _AI_GATE["t"])
        if wait > 0:
            time.sleep(wait)
        _AI_GATE["t"] = time.time()

        try:
            params = {
                "width": w, "height": h, "nologo": "true",
                "seed": random.randint(1, 1000000),
            }
            if model:
                params["model"] = model
            r = requests.get(
                "https://image.pollinations.ai/prompt/" + quote(full[:600]),
                params=params, headers=HEADERS, timeout=90,
            )
            _AI_GATE["t"] = time.time()
            if (
                r.status_code == 200
                and r.headers.get("content-type", "").startswith("image")
            ):
                with Image.open(io.BytesIO(r.content)) as img:
                    rgb = to_rgb(img)
                if rgb.width >= 256 and not is_blank(rgb):
                    rgb.save(save_path, "JPEG", quality=92)
                    return True
                _note(err, "AI ने खाली/बेकार तस्वीर दी")
            elif r.status_code == 429:
                _note(err, "AI सेवा अभी व्यस्त है (429)")
            else:
                _note(err, f"AI सेवा ने {r.status_code} दिया")
        except Exception as e:
            _note(err, f"AI: {type(e).__name__}: {str(e)[:70]}")
    return False


def _title_card(img, title):
    """रंगीन पृष्ठभूमि पर विषय का नाम बड़े अक्षरों में लिखता है (सिर्फ़ English)।"""
    title = re.sub(r"[^A-Za-z0-9 ,.&'-]", "", title or "").strip().upper()
    if not title:
        return img
    try:
        from PIL import ImageDraw
        font_path = get_caption_font()
        if not font_path:
            return img
        w, h = img.size
        fsize = max(24, int(min(w, h) * 0.10))
        font = ImageFont.truetype(font_path, fsize)
        draw = ImageDraw.Draw(img)

        lines, cur = [], ""
        for word in title.split():
            trial = (cur + " " + word).strip()
            if cur and draw.textlength(trial, font=font) > w * 0.8:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        if cur:
            lines.append(cur)

        lh = int(fsize * 1.25)
        y = (h - lh * len(lines)) // 2
        for ln in lines[:4]:
            tw = draw.textlength(ln, font=font)
            x = (w - tw) // 2
            draw.text((x + 3, y + 3), ln, font=font, fill=(0, 0, 0))
            draw.text((x, y), ln, font=font, fill=(255, 255, 255))
            y += lh
    except Exception:
        pass
    return img


def make_fallback_image(path, size, pool=None, avoid=None, title=""):
    """तस्वीर न मिले तो पहले मिली किसी तस्वीर को दोहराएँ, नहीं तो शीर्षक-कार्ड।"""
    pool = [p for p in (pool or []) if p and os.path.exists(p)]
    other = [p for p in pool if p != avoid]
    chosen = other or pool  # रंगीन पृष्ठभूमि से बेहतर है मिली हुई तस्वीर दोहराना
    if chosen:
        shutil.copy(random.choice(chosen), path)
        return "पहले की तस्वीर"

    palettes = [
        ("#1b2a6b", "#b21f66"), ("#0f2027", "#2c7a7b"),
        ("#42275a", "#734b6d"), ("#232526", "#c9a227"),
    ]
    c1, c2 = random.choice(palettes)
    grad = Image.linear_gradient("L").resize(size)
    if random.random() < 0.5:
        grad = grad.rotate(90, expand=True).resize(size)
    card = _title_card(ImageOps.colorize(grad, c1, c2), title)
    card.save(path, "JPEG", quality=90)
    return "रंगीन पृष्ठभूमि"


def diagnose_services(pixabay_key=""):
    """हर बाहरी सेवा को एक छोटे परीक्षण से जाँचता है: [(नाम, ठीक?, विवरण)]"""
    rows = []

    en = translate_hi_en("नमस्ते दोस्तो")
    if GoogleTranslator is None:
        rows.append(("Google अनुवाद", False, "deep-translator इंस्टॉल नहीं है"))
    else:
        rows.append(("Google अनुवाद", bool(en.strip()), en[:40] or "जवाब नहीं आया"))

    for name, fn in (
        ("इंटरनेट खोज", web_urls),
        ("Wikimedia", wikimedia_urls),
        ("Openverse", openverse_urls),
    ):
        errs = []
        res = fn("Babylon Ishtar Gate", errs)
        rows.append((name, len(res) > 0,
                     f"{len(res)} मिलीं" + (f" | {errs[0]}" if errs else "")))

    if pixabay_key:
        errs = []
        res = pixabay_urls("Babylon", pixabay_key, errs)
        rows.append(("Pixabay", len(res) > 0,
                     f"{len(res)} मिलीं" + (f" | {errs[0]}" if errs else "")))

    try:
        r = requests.get(
            "https://text.pollinations.ai/" + quote("Say hello in one word"),
            headers=HEADERS, timeout=30,
        )
        rows.append(("AI लेखन (Pollinations)",
                     r.status_code == 200 and bool(r.text.strip()),
                     f"जवाब कोड {r.status_code}"))
    except Exception as e:
        rows.append(("AI लेखन (Pollinations)", False, str(e)[:80]))

    errs = []
    tmp = os.path.join(tempfile.gettempdir(), "ravi_diag.jpg")
    t0 = time.time()
    ok = ai_image(
        "A beautiful ancient city wall at sunrise, wide photograph", tmp, (512, 512), errs
    )
    rows.append(("AI तस्वीर (Pollinations)", ok,
                 f"{time.time() - t0:.0f} सेकंड" + (f" | {errs[-1]}" if errs else "")))
    return rows


def collect_photos(query, folder, tag, key, web_on, modern, n=6):
    """हाथ से खोज: एक साथ कई विकल्प लौटाता है [(फ़ाइल, स्रोत)] और लॉग।"""
    words = match_words(query, "")
    sources = []
    if key:
        sources.append(("Pixabay", lambda e: pixabay_urls(query, key, e)))
    if not modern:
        sources.append(("Wikimedia", lambda e: wikimedia_urls(query, e)))
    if web_on:
        sources.append(("इंटरनेट", lambda e: web_urls(query, e)))
    sources.append(("Openverse", lambda e: openverse_urls(query, e)))

    found, seen, log = [], set(), []
    for name, fetch in sources:
        if len(found) >= n:
            break
        errs = []
        cands = fetch(errs)
        scored = []
        for url, text in cands:
            if not url or url in seen:
                continue
            sc = score_text(text, words) if words else 1
            if sc >= 1:
                scored.append((sc, url))
        scored.sort(key=lambda x: -x[0])
        log.append(
            f"{name}: {len(cands)} मिलीं, {len(scored)} मेल खाईं"
            + (f" | समस्या: {errs[0]}" if errs else "")
        )

        tries = 0
        for _, url in scored:
            if len(found) >= n or tries >= 10:
                break
            tries += 1
            seen.add(url)
            try:
                r = requests.get(url, headers=HEADERS, timeout=25)
                r.raise_for_status()
                if len(r.content) > 15_000_000:
                    continue
                with Image.open(io.BytesIO(r.content)) as img:
                    rgb = to_rgb(img)
                if rgb.width < 400 or rgb.height < 300 or is_blank(rgb):
                    continue
                rgb.thumbnail((2000, 2000))
                path = os.path.join(folder, f"cand_{tag}_{len(found)}.jpg")
                rgb.save(path, "JPEG", quality=88)
                found.append((path, name))
            except Exception:
                continue
    return found, log

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
    """एक साथ 3 दृश्यों की आवाज़ बनाता है (तेज़)।"""
    sem = asyncio.Semaphore(3)
    results = [None] * len(scenes)
    counter = {"n": 0}

    async def one(i, text):
        async with sem:
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
            results[i] = (path, bounds)
            counter["n"] += 1
            on_progress(counter["n"] - 1)

    await asyncio.gather(*[one(i, t) for i, t in enumerate(scenes)])
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


def write_ass(path, cues, size, font_path, size_ratio, lead,
              title=None, title_style="pop", title_pos="top", title_end=3.4):
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
        f"Style: Title,{family},{int(fontsize * 1.35)},&H0000E0FF,&H000000FF,"
        f"&H00000000,&H80000000,{0 if is_bold else -1},0,0,0,100,100,0,0,1,"
        f"{outline + 1},3,{8 if title_pos == 'top' else 5},{margin_lr},{margin_lr},"
        f"{int(h * 0.10)},1",
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

    if title and title.strip():
        clean_t = title.strip().replace("\\", "").replace("{", "(").replace("}", ")")
        clean_t = clean_t.replace("\n", "\\N")
        cx = w // 2
        ty = int(h * 0.10) if title_pos == "top" else h // 2
        if title_style == "slide":
            tag = "{\\move(%d,%d,%d,%d,0,320)\\fad(0,150)}" % (cx, -int(h * 0.1), cx, ty)
        elif title_style == "fade":
            tag = "{\\fad(400,300)}"
        else:
            tag = "{\\fad(120,120)\\fscx60\\fscy60\\t(0,260,\\fscx100\\fscy100)}"
        lines.append(
            f"Dialogue: 1,{ass_time(lead + 0.15)},{ass_time(lead + title_end)},"
            f"Title,,0,0,0,,{tag}{clean_t}"
        )

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return True


# ---------------------------------------------------------------- वीडियो क्लिप
ZOOM = 1.22  # अधिकतम ज़ूम


def motion_filter(effect, frames, w, h):
    # धीमी शुरुआत और धीमा अंत (ease in-out), ताकि हलचल सिनेमा जैसी लगे
    p = f"(on/{frames})"
    e = f"({p}*{p}*(3-2*{p}))"
    cx = "iw/2-(iw/zoom/2)"
    cy = "ih/2-(ih/zoom/2)"

    if effect == "Zoom Out":
        z, x, y = f"{ZOOM}-{ZOOM - 1}*{e}", cx, cy
    elif effect == "Pan Left to Right":
        z = f"{ZOOM}"
        x = f"(iw-iw/zoom)*{e}"
        y = "(ih-ih/zoom)/2"
    elif effect == "Pan Right to Left":
        z = f"{ZOOM}"
        x = f"(iw-iw/zoom)*(1-{e})"
        y = "(ih-ih/zoom)/2"
    else:  # Zoom In
        z, x, y = f"1+{ZOOM - 1}*{e}", cx, cy

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
        z, x, y = f"{ZOOM}", "iw-iw/zoom", "(ih-ih/zoom)/2"
    elif effect == "Pan Right to Left":
        z, x, y = f"{ZOOM}", "0", "(ih-ih/zoom)/2"
    else:
        z, x, y = f"{ZOOM}", cx, cy

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
              pad, is_last, title="", title_style="pop", title_pos="top"):
    w, h = size
    total = LEAD + voice_len + pad
    frames = max(1, math.ceil(total * FPS))
    k = 1.5 if max(w, h) <= 1300 else 1.3
    bw, bh = int(w * k) // 2 * 2, int(h * k) // 2 * 2

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

    if (cues or (title and title.strip())) and font:
        ass_path = os.path.join(
            folder, f"cap_{hashlib.md5(out.encode()).hexdigest()[:8]}.ass"
        )
        if write_ass(
            ass_path, cues or [], size, font, size_ratio, LEAD,
            title=title, title_style=title_style, title_pos=title_pos,
            title_end=max(1.0, min(total - 0.3 - LEAD, 3.4)),
        ):
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
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
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
from concurrent.futures import ThreadPoolExecutor, as_completed


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
    P["heartbeat"] = time.time()
    if P.get("cancel"):
        raise RuntimeError("आपके कहने पर रोक दिया गया।")
    if status is not None:
        P["status"] = status
    if progress is not None:
        P["progress"] = int(progress)


def publish_static(P, path):
    """वीडियो की ऐसी कॉपी रखें जिसका सीधा डाउनलोड लिंक बन सके।"""
    P["static_name"] = ""
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        d = os.path.join(base, "static")
        os.makedirs(d, exist_ok=True)
        for old in os.listdir(d):
            op = os.path.join(d, old)
            if old.startswith("ravi_") and time.time() - os.path.getmtime(op) > 6 * 3600:
                os.remove(op)
        name = f"ravi_{P['id']}_{int(time.time())}.mp4"
        shutil.copy(path, os.path.join(d, name))
        P["static_name"] = name
    except Exception:
        P["static_name"] = ""


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
                    "title": "",
                    "title_style": "pop",
                    "title_pos": "top",
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

        # 3) तस्वीरें: पहले सब दृश्यों की खोज साथ-साथ (तेज़), फिर बचे हुए के लिए AI
        used = P["used"]
        uploads = cfg["uploads"]
        lib = P.setdefault("library", [])

        todo = []
        for i, sc in enumerate(scenes):
            if sc.get("image") and os.path.exists(sc["image"]):
                continue
            path = os.path.join(folder, f"scene_{i}_0.jpg")
            if i < len(uploads):
                shutil.copy(uploads[i], path)
                sc.update(image=path, source="आपकी तस्वीर", query="")
                lib.append({"path": path, "label": "आपकी तस्वीर"})
                continue
            todo.append(i)

        def search_scene(i):
            sc = scenes[i]
            path = os.path.join(folder, f"scene_{i}_0.jpg")
            log = []
            plan = plans[i] if i < len(plans) else None
            english, exclude = "", []
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
                anchor, exclude=exclude, modern=modern, log=log,
            )
            return {"i": i, "path": path, "source": source, "query": query,
                    "log": log, "plan": plan, "english": english,
                    "exclude": exclude}

        found = {}
        if todo:
            done_n = 0
            with ThreadPoolExecutor(max_workers=3) as pool:
                futs = [pool.submit(search_scene, i) for i in todo]
                try:
                    for fut in as_completed(futs):
                        r = fut.result()
                        found[r["i"]] = r
                        done_n += 1
                        set_state(
                            P, f"तस्वीरें खोज रहे हैं: {done_n}/{len(todo)}",
                            27 + done_n / len(todo) * 45,
                        )
                except BaseException:
                    for f_ in futs:
                        f_.cancel()
                    raise

        last_good = None
        for i, sc in enumerate(scenes):
            if i not in found:
                if sc.get("image") and os.path.exists(sc["image"]):
                    last_good = sc["image"]
                continue

            r = found[i]
            path, source, query = r["path"], r["source"], r["query"]
            log, plan, english, exclude = r["log"], r["plan"], r["english"], r["exclude"]
            ai_err = []
            set_state(P, f"बाकी तस्वीरें पूरी कर रहे हैं: दृश्य {i + 1}/{n}",
                      72 + (i + 1) / n * 26)

            if not source and cfg["use_ai"]:
                set_state(P, f"मेल खाती तस्वीर नहीं मिली, AI से बना रहे हैं: दृश्य {i + 1}")
                if plan and plan.get("ai"):
                    prompt = str(plan["ai"])
                else:
                    prompt = visual_prompt(sc["text"], topic, anchor, english)
                if prompt and ai_image(prompt, path, size, ai_err):
                    source, query = "AI", prompt[:120]
                elif not prompt:
                    ai_err.append("दृश्य का तस्वीर-विवरण नहीं बन सका")

            if not source and anchor:
                set_state(P, f"विषय से जुड़ी सामान्य तस्वीर खोज रहे हैं: दृश्य {i + 1}")
                source, query = find_photo(
                    [anchor], path, used, cfg["pixabay_key"], cfg["use_web"],
                    anchor, exclude=exclude, modern=modern, relaxed=True,
                    log=log,
                )

            if source:
                sc.update(image=path, source=source, query=query)
                last_good = path
                P.setdefault("good", []).append(path)
                lib.append({"path": path, "label": f"{source}: {query[:40]}"})
            else:
                label = make_fallback_image(
                    path, size, P.get("good"), avoid=last_good, title=anchor
                )
                sc.update(image=path, source=label, query="")
                P.setdefault("missing", []).append(i + 1)

            if ai_err and sc["source"] != "AI":
                log.append("AI: " + "; ".join(sorted(set(ai_err))))
            sc["log"] = log
            save_project(P)

        if P.get("missing"):
            P["note"] = (P.get("note", "") +
                         f" इन दृश्यों की मेल खाती तस्वीर नहीं मिली: {sorted(set(P['missing']))}। "
                         "इनमें पहले की तस्वीर या रंगीन पृष्ठभूमि लगी है। "
                         "कारण जानने के लिए 'तस्वीर कैसे चुनी गई' खोलें।").strip()

        P["draft_done"] = True
        set_state(P, "ड्राफ़्ट तैयार है", 100)
        P["state"] = "rendering" if cfg.get("chain") else "ready"
        save_project(P)

    except Exception as e:
        P["state"] = "error"
        P["error"] = str(e)
        save_project(P)


def resolve_effects(kept, off, default_effect, default_transition):
    """रखे गए हर दृश्य का असली इफेक्ट और ट्रांज़िशन (Default/Auto सुलझाकर)।"""
    effects, trans = [], []
    for idx, sc in enumerate(kept):
        e = sc["effect"] if sc["effect"] != "Default" else default_effect
        if e.startswith("Auto"):
            e = EFFECTS[(idx + off) % len(EFFECTS)]
        t = sc["transition"] if sc["transition"] != "Default" else default_transition
        if t.startswith("Auto"):
            t = TRANSITIONS[(idx * 5 + off) % len(TRANSITIONS)]
        effects.append(e)
        trans.append(t)
    return effects, trans


def preview_clip(P, sel, size, default_effect, default_transition, captions, cap_ratio):
    """एक दृश्य की छोटी झलक (कम क्वालिटी, जल्दी बनती है)।"""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    scenes = P["scenes"]
    kept = [s_ for s_ in scenes if s_["keep"] or s_ is scenes[sel]]
    pos = next(i for i, s_ in enumerate(kept) if s_ is scenes[sel])
    effects, trans = resolve_effects(
        kept, P.get("offset", 0), default_effect, default_transition
    )
    sc = kept[pos]
    prev = kept[pos - 1]["image"] if pos > 0 else None
    prev_eff = effects[pos - 1] if pos > 0 else None
    font = get_caption_font() if captions else None
    out = os.path.join(P["folder"], f"preview_{int(time.time() * 1000) % 10**9}.mp4")
    make_clip(
        sc["image"], prev, prev_eff, sc["voice_path"], sc["voice_len"],
        sc["cues"] if font else None, out, size, effects[pos], trans[pos],
        ffmpeg, font, cap_ratio, P["folder"], sc["pad"], False,
        title=(sc.get("title") or "").strip(),
        title_style=sc.get("title_style", "pop"),
        title_pos=sc.get("title_pos", "top"),
    )
    return out


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
        effects, trans = resolve_effects(
            kept, off, cfg["default_effect"], cfg["default_transition"]
        )

        plan_jobs = []
        for idx, sc in enumerate(kept):
            prev = kept[idx - 1]["image"] if idx > 0 else None
            prev_eff = effects[idx - 1] if idx > 0 else None
            title = (sc.get("title") or "").strip()
            sig = repr((
                sc["image"], os.path.getmtime(sc["image"]),
                prev, prev_eff, effects[idx], trans[idx] if prev else "",
                sc["voice_path"], os.path.getmtime(sc["voice_path"]),
                sc["cues"] if font else None, font, size,
                cfg["cap_ratio"], sc["pad"], idx == n - 1,
                title, sc.get("title_style"), sc.get("title_pos"),
            ))
            key = hashlib.md5(sig.encode()).hexdigest()[:16]
            plan_jobs.append({
                "idx": idx, "sc": sc, "prev": prev, "prev_eff": prev_eff,
                "effect": effects[idx], "trans": trans[idx], "title": title,
                "clip": os.path.join(folder, f"clip_{key}.mp4"),
            })

        pending = [j for j in plan_jobs if not os.path.exists(j["clip"])]
        reused = len(plan_jobs) - len(pending)

        def build(j):
            sc = j["sc"]
            tmp = j["clip"] + ".part.mp4"
            make_clip(
                sc["image"], j["prev"], j["prev_eff"], sc["voice_path"],
                sc["voice_len"], sc["cues"] if font else None, tmp,
                size, j["effect"], j["trans"], ffmpeg, font,
                cfg["cap_ratio"], folder, sc["pad"], j["idx"] == n - 1,
                title=j["title"], title_style=sc.get("title_style", "pop"),
                title_pos=sc.get("title_pos", "top"),
            )
            os.replace(tmp, j["clip"])

        if pending:
            workers = 2 if (os.cpu_count() or 2) <= 2 else min(4, (os.cpu_count() or 4) // 2)
            msg = f"{reused} क्लिप पहले से बने मिले, " if reused else ""
            done_n = 0
            set_state(P, f"{msg}{len(pending)} क्लिप बन रहे हैं...", 2)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [pool.submit(build, j) for j in pending]
                try:
                    for fut in as_completed(futs):
                        fut.result()
                        done_n += 1
                        set_state(
                            P, f"{msg}क्लिप बने: {done_n}/{len(pending)}",
                            2 + done_n / len(pending) * 88,
                        )
                except BaseException:
                    for f_ in futs:
                        f_.cancel()
                    raise
        else:
            set_state(P, f"सभी {n} क्लिप पहले से बने हुए हैं", 90)

        clips = [j["clip"] for j in plan_jobs]

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
        publish_static(P, final)
        set_state(P, "वीडियो तैयार है!", 100)
        P["state"] = "done"
        save_project(P)

    except Exception as e:
        P["state"] = "error"
        P["error"] = str(e)


def full_job(P, cfg):
    """कहानी से तैयार वीडियो तक पूरा काम (रुकने पर वहीं से आगे बढ़ता है)।"""
    P["cancel"] = False
    if not P.get("draft_done"):
        draft_job(P, dict(cfg, chain=True))
        if not P.get("draft_done"):
            return
    P["state"] = "rendering"
    P["progress"] = 0
    render_job(P, cfg["render"])


def make_silent_audio(path, seconds, ffmpeg):
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
        "-t", str(seconds), "-q:a", "9", path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)


def new_scene(P, text, image, source="आपकी तस्वीर", query=""):
    """नया दृश्य: शब्द हों तो आवाज़ बनती है, नहीं तो 3 सेकंड की चुप्पी।"""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    uid = int(time.time() * 1000) % 10**9
    text = (text or "").strip()
    if text:
        vp = os.path.join(P["folder"], f"voice_new_{uid}.mp3")
        rate, pitch = voice_params(text, P.get("speed", -5), P.get("natural", True))
        bounds = asyncio.run(synth(text, P["voice"], vp, rate, pitch))
        vlen = audio_duration(vp, ffmpeg)
        cues = build_cues(text, bounds, vlen)
    else:
        vp = os.path.join(P["folder"], f"voice_new_{uid}.mp3")
        make_silent_audio(vp, 3.0, ffmpeg)
        vlen, cues = 3.0, []
    return {
        "text": text or "(बिना आवाज़ का दृश्य)", "voice_path": vp, "voice_len": vlen,
        "cues": cues, "pad": 0.35, "image": image, "source": source,
        "query": query, "effect": "Default", "transition": "Default",
        "keep": True, "title": "", "title_style": "pop", "title_pos": "top",
        "log": [],
    }


def start_thread(fn, P, cfg):
    t = threading.Thread(target=fn, args=(P, cfg), daemon=True)
    t.start()



# ==== UI ====
import base64
import copy

QUALITIES = ["HD 1080p (बेहतर, धीमा)", "सामान्य 720p (तेज़)"]
EFFECT_NAMES = {
    "Default": "सेटिंग वाला (डिफ़ॉल्ट)",
    "Zoom In": "धीरे-धीरे पास आना (Zoom In)",
    "Zoom Out": "धीरे-धीरे दूर जाना (Zoom Out)",
    "Pan Left to Right": "बाएँ से दाएँ घूमना (Pan →)",
    "Pan Right to Left": "दाएँ से बाएँ घूमना (Pan ←)",
}
TRANS_NAMES = {
    "Default": "सेटिंग वाला (डिफ़ॉल्ट)",
    "fade": "धीरे से बदलना (Fade)",
    "slideleft": "बाएँ खिसकना",
    "slideright": "दाएँ खिसकना",
    "wipeleft": "बाएँ पोंछना",
    "wiperight": "दाएँ पोंछना",
    "circleopen": "गोला खुलना",
    "dissolve": "घुल जाना",
    "smoothright": "दाएँ चिकना",
    "slideup": "ऊपर खिसकना",
    "slidedown": "नीचे खिसकना",
    "radial": "घूमता हुआ खुलना",
    "zoomin": "ज़ूम वाला बदलाव",
    "fadewhite": "सफ़ेद चमक",
}
TITLE_STYLES = {"pop": "उभरकर आना (Pop)", "slide": "ऊपर से सरकना (Slide)", "fade": "धीरे से आना (Fade)"}
TITLE_POS = {"top": "ऊपर", "center": "बीच में"}

DEFAULTS = {
    "script": "", "voice_name": list(VOICES.keys())[0], "speed": -5, "natural": True,
    "format_name": list(FORMATS.keys())[0], "quality": QUALITIES[0], "max_chars": 110,
    "use_web": True, "use_ai": True, "extra_topic": "",
    "captions_on": True, "cap_name": "मध्यम",
    "effect_choice": "Auto (हर दृश्य में अलग)", "transition_choice": "Auto (हर दृश्य अलग)",
    "gemini_key": "", "pixabay_key": "",
}
S = st.session_state.setdefault("S", dict(DEFAULTS))
for _k, _v in DEFAULTS.items():
    S.setdefault(_k, _v)


def secret(name):
    """Streamlit के Secrets में एक बार रखी key पढ़ता है (GitHub में नहीं दिखती)।"""
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def secret_names():
    try:
        return list(st.secrets.keys())
    except Exception:
        return []


def eff_gemini():
    return S["gemini_key"].strip() or secret("GEMINI_API_KEY")


def eff_pixabay():
    return S["pixabay_key"].strip() or secret("PIXABAY_API_KEY")


def get_thumb(path):
    """छोटी झलक बनाकर रखें, ताकि टाइमलाइन तेज़ खुले।"""
    try:
        tp = path + ".thumb.jpg"
        if not os.path.exists(tp):
            with Image.open(path) as im:
                im = to_rgb(im)
                im.thumbnail((240, 240))
                im.save(tp, "JPEG", quality=80)
        return tp
    except Exception:
        return path


def logo_b64():
    try:
        with Image.open(LOGO_PATH) as im:
            im = im.convert("RGBA")
            im.thumbnail((112, 112))
            buf = io.BytesIO()
            im.save(buf, "PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


STYLE = """
<style>
.rs-hero{display:flex;align-items:center;gap:14px;padding:16px 18px;border-radius:18px;
  background:linear-gradient(135deg,#6a11cb 0%,#2575fc 55%,#ff4d8d 100%);color:#fff;margin:0 0 10px 0}
.rs-hero img{width:58px;height:58px;border-radius:14px}
.rs-title{font-size:1.5rem;font-weight:800;line-height:1.1}
.rs-sub{font-size:.85rem;opacity:.93;margin-top:3px}
.rs-chip{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.74rem;font-weight:700;
  background:rgba(106,17,203,.20);margin-right:6px}
.rs-ok{background:rgba(24,160,88,.22)} .rs-warn{background:rgba(240,160,0,.25)} .rs-bad{background:rgba(229,72,77,.25)}
div.stButton>button, div.stDownloadButton>button{border-radius:12px;font-weight:600}
</style>
"""
st.markdown(STYLE, unsafe_allow_html=True)
_lg = logo_b64()
st.markdown(
    f'<div class="rs-hero">{f"<img src=data:image/png;base64,{_lg}>" if _lg else ""}'
    '<div><div class="rs-title">Ravi Studio</div>'
    '<div class="rs-sub">AI वीडियो क्रिएटर • कहानी से तैयार वीडियो</div></div></div>',
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------- स्थिति
def is_busy(p):
    return p["state"] in ("drafting", "rendering")


# अटका हुआ काम पहचानें (15 मिनट से कोई हलचल नहीं)
for _p in JOBS.values():
    if is_busy(_p) and time.time() - _p.get("heartbeat", _p.get("created", time.time())) > 900:
        _p["state"] = "error"
        _p["error"] = "काम बहुत देर से अटका हुआ था, इसलिए रोक दिया गया।"

pid = st.query_params.get("p")
P = JOBS.get(pid) if pid else None
if P is None and JOBS and not st.session_state.get("fresh"):
    P = max(JOBS.values(), key=lambda p: p.get("created", 0))
    st.query_params["p"] = P["id"]

busy_p = next((p for p in JOBS.values() if is_busy(p)), None)
busy_any = busy_p is not None

PAGES = ["🏠 होम", "✍️ नया", "🎞️ एडिटर", "🤖 AI फोटो", "⚙️ सेटिंग"]
if "_page_next" in st.session_state:
    st.session_state["page"] = st.session_state.pop("_page_next")
st.session_state.setdefault("page", PAGES[0])
if st.session_state["page"] not in PAGES:
    st.session_state["page"] = PAGES[0]


def goto(page_name):
    st.session_state["page"] = page_name


def open_project(project_id, page_name="🎞️ एडिटर"):
    st.query_params["p"] = project_id
    st.session_state["fresh"] = False
    st.session_state["page"] = page_name
    st.session_state.pop("sel", None)


def delete_project(project_id):
    p = JOBS.pop(project_id, None)
    if p:
        shutil.rmtree(p.get("folder", ""), ignore_errors=True)
    if st.query_params.get("p") == project_id:
        st.query_params.clear()
        st.session_state["fresh"] = True


page = st.radio("मेन्यू", PAGES, horizontal=True, key="page", label_visibility="collapsed")

# चल रहे काम की पट्टी (हर पन्ने पर)
if busy_any:
    with st.container(border=True):
        st.markdown(f"⏳ **{busy_p.get('title', 'प्रोजेक्ट')}** बन रहा है")
        st.progress(min(100, busy_p["progress"]))
        st.caption(busy_p["status"])
        st.caption(
            "💡 काम सर्वर पर चलता है, फ़ोन की स्क्रीन बंद हो तो भी चलता रहेगा। "
            "रुक जाए तो बना हुआ काम दोबारा नहीं बनेगा।"
        )
        if st.button("⏹️ रोकें", key="stop_btn"):
            busy_p["cancel"] = True
            busy_p["status"] = "रोका जा रहा है..."


# ---------------------------------------------------------------- cfg बनाना
def sizes():
    base = FORMATS[S["format_name"]]
    hd = (base[0] * 3 // 2, base[1] * 3 // 2) if S["quality"].startswith("HD") else base
    fast = (base[0] * 2 // 3 // 2 * 2, base[1] * 2 // 3 // 2 * 2)
    return hd, fast


def render_cfg(fast=False, music=None, vol=0.12):
    hd, fast_size = sizes()
    return {
        "size": fast_size if fast else hd,
        "default_effect": S["effect_choice"],
        "default_transition": S["transition_choice"],
        "captions": S["captions_on"], "cap_ratio": CAPTION_SIZES[S["cap_name"]],
        "music": music, "music_vol": vol,
    }


def make_cfg(resume=False, uploads=None):
    hd, _ = sizes()
    return {
        "resume": resume, "voice": VOICES[S["voice_name"]], "speed": S["speed"],
        "natural": S["natural"], "max_chars": S["max_chars"], "size": hd,
        "use_web": S["use_web"], "use_ai": S["use_ai"],
        "gemini_key": eff_gemini(), "pixabay_key": eff_pixabay(),
        "extra_topic": S["extra_topic"], "uploads": uploads or [],
        "render": render_cfg(),
    }


def status_chip(p):
    if is_busy(p):
        return '<span class="rs-chip rs-warn">⏳ बन रहा है</span>'
    if p.get("video") and os.path.exists(p["video"]):
        return '<span class="rs-chip rs-ok">✅ वीडियो तैयार</span>'
    if p.get("draft_done"):
        return '<span class="rs-chip">📝 एडिट के लिए तैयार</span>'
    return '<span class="rs-chip rs-bad">⚠️ अधूरा</span>'


def apply_image(sel, src_path, source, query):
    """चुनी तस्वीर की नई कॉपी लगाएँ (ताकि पुरानी वापस मिल सके)।"""
    sc = P["scenes"][sel]
    newp = os.path.join(P["folder"], f"scene_{sel}_{int(time.time() * 1000) % 10**9}.jpg")
    shutil.copy(src_path, newp)
    sc.setdefault("history", []).append((sc["image"], sc["source"], sc["query"]))
    sc.update(image=newp, source=source, query=query)
    save_project(P)


def add_to_library(path, label):
    P.setdefault("library", []).append({"path": path, "label": label})


# ================================================================ पन्ना: होम
if page == "🏠 होम":
    st.subheader("मेरे प्रोजेक्ट")
    st.button("➕ नया वीडियो बनाएँ", type="primary", on_click=goto, args=("✍️ नया",),
              use_container_width=True)

    projects = sorted(JOBS.values(), key=lambda p: p.get("created", 0), reverse=True)
    if not projects:
        st.info("अभी कोई प्रोजेक्ट नहीं है। ऊपर के बटन से पहला वीडियो शुरू करें।")
    for p in projects:
        with st.container(border=True):
            when = time.strftime("%d %b, %H:%M", time.localtime(p.get("created", time.time())))
            st.markdown(f"**{p.get('title') or p.get('script', '')[:30] or 'बिना नाम'}**")
            st.markdown(
                status_chip(p)
                + f'<span class="rs-chip">🎞️ {len(p.get("scenes", []))} दृश्य</span>'
                + f'<span class="rs-chip">🕒 {when}</span>',
                unsafe_allow_html=True,
            )
            c1, c2 = st.columns(2)
            c1.button("✏️ खोलें / एडिट करें", key=f"open_{p['id']}", on_click=open_project,
                      args=(p["id"],), use_container_width=True)
            if st.session_state.get("del") == p["id"]:
                st.warning("पक्का हटाएँ? यह वापस नहीं आएगा।")
                d1, d2 = st.columns(2)
                if d1.button("हाँ, हटाएँ", key=f"yes_{p['id']}"):
                    delete_project(p["id"])
                    st.session_state.pop("del", None)
                    st.rerun()
                if d2.button("रहने दें", key=f"no_{p['id']}"):
                    st.session_state.pop("del", None)
                    st.rerun()
            else:
                if c2.button("🗑️ हटाएँ", key=f"del_{p['id']}", use_container_width=True):
                    st.session_state["del"] = p["id"]
                    st.rerun()

    st.caption(
        "ℹ️ प्रोजेक्ट सर्वर पर कुछ घंटे रहते हैं। ज़रूरी वीडियो बनते ही डाउनलोड कर लें।"
    )

# ================================================================ पन्ना: नया वीडियो
elif page == "✍️ नया":
    st.subheader("नया वीडियो")
    S["script"] = st.text_area(
        "पूरी हिंदी कहानी लिखें", value=S["script"], height=240, key="w_script"
    )

    S["voice_name"] = st.radio(
        "🎙️ आवाज़", list(VOICES.keys()), horizontal=True,
        index=list(VOICES.keys()).index(S["voice_name"]), key="w_voice",
    )
    S["speed"] = st.slider("बोलने की गति", -25, 15, S["speed"], 1, key="w_speed",
                           help="ऋण का मतलब थोड़ा धीरे")
    S["natural"] = st.checkbox(
        "🗣️ प्राकृतिक आवाज़ (हर दृश्य में थोड़ी अलग गति/सुर)", value=S["natural"], key="w_nat"
    )
    if st.button("🔊 चुनी हुई आवाज़ सुनें"):
        try:
            sample = os.path.join(
                tempfile.gettempdir(), f"ravi_sample_{int(time.time())}.mp3"
            )
            asyncio.run(synth(
                "नमस्ते दोस्तो, आज हम एक बहुत रोचक कहानी सुनेंगे।",
                VOICES[S["voice_name"]], sample, f"{int(S['speed']):+d}%", "+0Hz",
            ))
            with open(sample, "rb") as f:
                st.audio(f.read(), format="audio/mp3")
        except Exception as e:
            st.error(f"आवाज़ का नमूना नहीं बन सका: {e}")

    c1, c2 = st.columns(2)
    S["format_name"] = c1.selectbox(
        "📐 वीडियो का आकार", list(FORMATS.keys()),
        index=list(FORMATS.keys()).index(S["format_name"]), key="w_fmt",
    )
    S["quality"] = c2.selectbox(
        "🎞️ क्वालिटी", QUALITIES, index=QUALITIES.index(S["quality"]), key="w_q"
    )
    S["max_chars"] = st.slider(
        "✂️ एक दृश्य में अधिकतम अक्षर (कम = तस्वीरें जल्दी-जल्दी बदलेंगी)",
        60, 400, S["max_chars"], 10, key="w_max",
    )
    S["use_web"] = st.checkbox(
        "🌐 इंटरनेट की तस्वीरों में खोजें (कॉपीराइट आपकी ज़िम्मेदारी है)",
        value=S["use_web"], key="w_web",
    )
    S["use_ai"] = st.checkbox(
        "🤖 मेल खाती तस्वीर न मिले तो AI से बनाएँ", value=S["use_ai"], key="w_ai"
    )
    S["extra_topic"] = st.text_input(
        "कहानी का मुख्य विषय English में (वैकल्पिक, हर खोज में जुड़ेगा)",
        value=S["extra_topic"], placeholder="Babylon  /  India vs Australia ODI 2026",
        key="w_topic",
    )
    uploaded_images = st.file_uploader(
        "🖼️ अपनी तस्वीरें (पहले दृश्यों में क्रम से लगेंगी)",
        accept_multiple_files=True, key="w_up",
    )
    if uploaded_images:
        st.caption(f"✅ {len(uploaded_images)} फ़ाइल मिली")

    if not eff_gemini():
        st.caption("💡 सटीक तस्वीरों के लिए ⚙️ सेटिंग में मुफ़्त Gemini key डालें।")

    if st.button("🎬 वीडियो बनाना शुरू करें", type="primary",
                 disabled=busy_any, use_container_width=True):
        if not S["script"].strip():
            st.error("पहले कहानी लिखें।")
            st.stop()

        new_id = hashlib.md5(f"{time.time()}{random.random()}".encode()).hexdigest()[:10]
        folder = tempfile.mkdtemp(prefix="ravi_video_")

        uploads, bad = [], []
        for i, up in enumerate(uploaded_images or []):
            try:
                p = os.path.join(folder, f"upload_{i}.jpg")
                with Image.open(up) as img:
                    rgb = to_rgb(img)
                rgb.thumbnail((2400, 2400))
                rgb.save(p, "JPEG", quality=92)
                uploads.append(p)
            except Exception:
                bad.append(up.name)
        if bad:
            st.warning("ये फ़ाइलें तस्वीर के रूप में नहीं खुलीं और छोड़ दी गईं: " + ", ".join(bad))

        hd, _ = sizes()
        P = {
            "id": new_id, "folder": folder, "state": "drafting",
            "title": S["script"].strip().replace("\n", " ")[:32],
            "created": time.time(), "heartbeat": time.time(),
            "script": S["script"], "draft_done": False, "planned": False,
            "status": "शुरू हो रहा है...", "progress": 0,
            "note": "", "error": "", "scenes": [], "used": set(), "video": None,
            "anchor": "", "topic": "", "modern": False, "offset": 0,
            "voice": VOICES[S["voice_name"]], "speed": S["speed"], "natural": S["natural"],
            "size": hd, "cancel": False, "static_name": "", "library": [],
        }
        JOBS[new_id] = P
        st.session_state["fresh"] = False
        st.session_state["_page_next"] = "🏠 होम"
        st.session_state.pop("sel", None)
        start_thread(full_job, P, make_cfg(uploads=uploads))
        st.query_params["p"] = new_id
        st.rerun()

# ================================================================ पन्ना: एडिटर
elif page == "🎞️ एडिटर":
    if P is None:
        st.info("पहले कोई प्रोजेक्ट खोलें या नया बनाएँ।")
        st.button("🏠 प्रोजेक्ट देखें", on_click=goto, args=("🏠 होम",))
    elif is_busy(P):
        st.info("इस प्रोजेक्ट का काम चल रहा है। पूरा होने तक एडिटर बंद है, ऊपर प्रगति दिख रही है।")
    elif not P.get("draft_done"):
        done_n = sum(1 for sc in P["scenes"] if sc.get("image"))
        st.warning(
            f"ड्राफ़्ट अभी पूरा नहीं बना (तस्वीरें {done_n}/{len(P['scenes'])})। "
            "जो बन चुका है वह दोबारा नहीं बनेगा।"
        )
        if P.get("error"):
            st.code(P["error"][-500:])
        if st.button("▶️ वहीं से आगे बढ़ाएँ", type="primary", disabled=busy_any):
            P["error"], P["note"], P["cancel"] = "", "", False
            P["heartbeat"] = time.time()
            P["state"] = "drafting"
            start_thread(full_job, P, make_cfg(resume=True))
            st.rerun()
    else:
        scenes = P["scenes"]
        n = len(scenes)
        st.markdown(f"**{P.get('title', 'प्रोजेक्ट')}**")

        if P.get("note"):
            st.warning(P["note"])
        if P["state"] == "error" and P.get("error"):
            st.error("पिछली कोशिश में समस्या आई।")
            st.code(P["error"][-500:])

        # ---------- तैयार वीडियो
        video_ready = bool(P.get("video")) and os.path.exists(P["video"])
        if video_ready:
            with st.expander("▶️ तैयार वीडियो देखें और डाउनलोड करें", expanded=True):
                st.video(P["video"])
                if P.get("static_name"):
                    st.markdown(
                        f'<a href="app/static/{P["static_name"]}" download="ravi_video.mp4" '
                        'style="display:block;text-align:center;padding:0.7rem 1rem;'
                        'border-radius:0.6rem;background:#6a11cb;color:#fff;'
                        'font-weight:700;text-decoration:none;">'
                        "⬇️ वीडियो डाउनलोड करें</a>",
                        unsafe_allow_html=True,
                    )
                    st.caption(
                        "डाउनलोड के बाद वीडियो खुल जाए और वापसी का रास्ता न दिखे, "
                        "तो इसी ऐप को दोबारा खोलें। आपका वीडियो यहीं सुरक्षित रहेगा।"
                    )
                try:
                    with open(P["video"], "rb") as f:
                        st.download_button("⬇️ दूसरा तरीका: MP4", data=f,
                                           file_name="ravi_video.mp4", mime="video/mp4",
                                           on_click="ignore")
                except TypeError:
                    with open(P["video"], "rb") as f:
                        st.download_button("⬇️ दूसरा तरीका: MP4", data=f,
                                           file_name="ravi_video.mp4", mime="video/mp4")

        # ---------- चुना हुआ दृश्य (पहले से सेट, ताकि widget बनने से पहले बदला जा सके)
        if "_sel_next" in st.session_state:
            st.session_state["sel"] = st.session_state.pop("_sel_next")
        if "sel" not in st.session_state or st.session_state["sel"] >= n:
            st.session_state["sel"] = 0

        # ---------- टाइमलाइन
        st.markdown("**🎬 टाइमलाइन**")
        for r in range(0, n, 4):
            cols = st.columns(4)
            for c in range(4):
                i = r + c
                if i >= n:
                    break
                with cols[c]:
                    sc_ = scenes[i]
                    if sc_.get("image") and os.path.exists(sc_["image"]):
                        st.image(get_thumb(sc_["image"]), use_container_width=True)
                    mark = "🚫 " if not sc_["keep"] else ("▶ " if i == st.session_state["sel"] else "")
                    title_mark = " 🔠" if (sc_.get("title") or "").strip() else ""
                    st.caption(f"{mark}{i + 1}{title_mark}")

        def _prev():
            st.session_state["sel"] = max(0, st.session_state["sel"] - 1)

        def _next():
            st.session_state["sel"] = min(n - 1, st.session_state["sel"] + 1)

        b1, b2 = st.columns(2)
        b1.button("◀ पिछला", on_click=_prev, key="prev_btn", use_container_width=True)
        b2.button("अगला ▶", on_click=_next, key="next_btn", use_container_width=True)

        sel = st.selectbox(
            "कौन-सा दृश्य बदलना है?", list(range(n)), key="sel",
            format_func=lambda i: f"{i + 1}. {scenes[i]['text'][:34]}",
        )
        sc = scenes[sel]

        if sc.get("image") and os.path.exists(sc["image"]):
            st.image(sc["image"], use_container_width=True)
        st.caption(f"स्रोत: {sc['source']}  |  {sc['query'][:70]}")
        st.write(sc["text"])

        tab = st.radio(
            "क्या बदलना है?",
            ["🖼️ फोटो", "✨ एनिमेशन", "🔠 टाइटल", "📝 शब्द/आवाज़", "🧩 दृश्य"],
            horizontal=True, key="etab", label_visibility="collapsed",
        )

        # ============ फोटो
        if tab == "🖼️ फोटो":
            a1, a2 = st.columns(2)
            if a1.button("🗑️ यह फोटो हटाएँ", key=f"rm_{sel}", use_container_width=True):
                ph = os.path.join(P["folder"], f"scene_{sel}_blank_{int(time.time() * 1000) % 10**9}.jpg")
                make_fallback_image(ph, P["size"], [], None, title=P.get("anchor", ""))
                sc.setdefault("history", []).append((sc["image"], sc["source"], sc["query"]))
                sc.update(image=ph, source="खाली (नई फोटो लगाएँ)", query="")
                st.rerun()
            if sc.get("history") and a2.button("↩️ पिछली फोटो", key=f"undo_{sel}",
                                                use_container_width=True):
                img_p, src_p, q_p = sc["history"].pop()
                sc.update(image=img_p, source=src_p, query=q_p)
                st.rerun()

            st.markdown("**➕ नई फोटो लगाएँ**")
            mode = st.radio(
                "तरीका",
                ["📱 गैलरी से", "🌐 इंटरनेट से", "🤖 AI से", "📚 लाइब्रेरी से"],
                horizontal=True, key=f"mode_{sel}", label_visibility="collapsed",
            )

            if mode == "📱 गैलरी से":
                up = st.file_uploader("अपनी फोटो चुनें", key=f"up_{sel}")
                if up is not None:
                    sig = f"{up.name}-{up.size}"
                    if sc.get("up_sig") != sig:
                        try:
                            tmpf = os.path.join(P["folder"], f"upload_{sel}_{int(time.time())}.jpg")
                            with Image.open(up) as img:
                                rgb = to_rgb(img)
                            rgb.thumbnail((2400, 2400))
                            rgb.save(tmpf, "JPEG", quality=92)
                            sc["up_sig"] = sig
                            add_to_library(tmpf, "आपकी फोटो")
                            apply_image(sel, tmpf, "आपकी तस्वीर", "")
                            st.rerun()
                        except Exception:
                            st.error("यह फ़ाइल तस्वीर के रूप में नहीं खुली।")

            elif mode == "🌐 इंटरनेट से":
                q = st.text_input(
                    "English शब्द (जितने सटीक, उतना अच्छा)",
                    value=(sc["query"] if sc["source"] in ("इंटरनेट", "Wikimedia", "Openverse", "Pixabay") else ""),
                    key=f"q_{sel}", placeholder="Ishtar Gate Babylon",
                )
                ckey = f"cands_{P['id']}_{sel}"
                if st.button("🔍 विकल्प खोजें", key=f"s_{sel}", use_container_width=True):
                    with st.spinner("कई विकल्प खोज रहे हैं..."):
                        found, log = collect_photos(
                            q, P["folder"], f"{sel}_{int(time.time()) % 100000}",
                            eff_pixabay(), S["use_web"], P.get("modern", False), n=6,
                        )
                    st.session_state[ckey] = (q, found, log)
                if ckey in st.session_state:
                    q_used, found, log = st.session_state[ckey]
                    if not found:
                        st.warning("मेल खाती तस्वीर नहीं मिली। दूसरे शब्द आज़माएँ।")
                        st.code("\n".join(log) or "कोई स्रोत नहीं चला", language=None)
                    for k in range(0, len(found), 3):
                        cols = st.columns(3)
                        for j in range(3):
                            idx = k + j
                            if idx >= len(found):
                                break
                            path_, src_ = found[idx]
                            with cols[j]:
                                st.image(get_thumb(path_), use_container_width=True)
                                st.caption(src_)
                                if st.button("✅ लगाएँ", key=f"pick_{sel}_{idx}"):
                                    add_to_library(path_, f"{src_}: {q_used[:30]}")
                                    apply_image(sel, path_, src_, q_used)
                                    st.session_state.pop(ckey, None)
                                    st.rerun()

            elif mode == "🤖 AI से":
                ai_prompt = st.text_area(
                    "कैसी फोटो चाहिए? (हिंदी या English में)", value="",
                    key=f"aip_{sel}", height=90,
                    placeholder="खाली छोड़ें तो इस दृश्य के शब्दों से अपने आप बनेगी",
                )
                if st.button("🎨 AI से बनाएँ", key=f"a_{sel}", use_container_width=True):
                    errs = []
                    prompt = ai_prompt.strip()
                    if prompt and re.search(r"[\u0900-\u097F]", prompt):
                        prompt = translate_hi_en(prompt) or prompt
                    if len(prompt) < 15:
                        prompt = visual_prompt(sc["text"], P.get("topic", ""), P.get("anchor", ""))
                    newp = os.path.join(P["folder"], f"ai_{sel}_{int(time.time() * 1000) % 10**9}.jpg")
                    with st.spinner("AI फोटो बना रहा है (30-90 सेकंड लग सकते हैं)..."):
                        good = bool(prompt) and ai_image(prompt, newp, P["size"], errs)
                    if good:
                        add_to_library(newp, "AI: " + prompt[:40])
                        apply_image(sel, newp, "AI", prompt[:120])
                        st.rerun()
                    else:
                        st.warning("AI फोटो नहीं बन सकी। थोड़ी देर बाद दोबारा कोशिश करें।")
                        if errs:
                            st.code("\n".join(sorted(set(errs))), language=None)

            else:  # लाइब्रेरी
                lib = [e for e in P.get("library", []) if os.path.exists(e["path"])]
                if not lib:
                    st.info("लाइब्रेरी खाली है। '🤖 AI फोटो' पन्ने से फोटो बनाएँ या अपलोड करें।")
                else:
                    pick = st.selectbox(
                        "लाइब्रेरी से चुनें", list(range(len(lib))),
                        format_func=lambda i: f"{i + 1}. {lib[i]['label'][:40]}",
                        key=f"lib_{sel}",
                    )
                    st.image(get_thumb(lib[pick]["path"]), width=200)
                    if st.button("✅ इस दृश्य में लगाएँ", key=f"libapply_{sel}"):
                        apply_image(sel, lib[pick]["path"], "लाइब्रेरी", lib[pick]["label"][:60])
                        st.rerun()

        # ============ एनिमेशन
        elif tab == "✨ एनिमेशन":
            fx_keys = list(EFFECT_NAMES.keys())
            tr_keys = list(TRANS_NAMES.keys())
            sc["keep"] = st.checkbox("इस दृश्य को वीडियो में रखें", sc["keep"], key=f"keep_{sel}")
            sc["effect"] = st.selectbox(
                "🎥 कैमरा हलचल (ज़ूम/पैन)", fx_keys,
                index=fx_keys.index(sc["effect"]) if sc["effect"] in fx_keys else 0,
                format_func=lambda k: EFFECT_NAMES[k], key=f"eff_{sel}",
            )
            sc["transition"] = st.selectbox(
                "🔀 इस दृश्य में आते समय बदलाव (ट्रांज़िशन)", tr_keys,
                index=tr_keys.index(sc["transition"]) if sc["transition"] in tr_keys else 0,
                format_func=lambda k: TRANS_NAMES[k], key=f"tr_{sel}",
            )
            sc["pad"] = st.slider(
                "⏱️ दृश्य के अंत में ठहराव (सेकंड)", 0.2, 3.0, float(sc.get("pad", 0.35)), 0.1,
                key=f"pad_{sel}",
            )
            e1, e2 = st.columns(2)
            if e1.button("यह कैमरा हलचल सब पर", key=f"fxall_{sel}", use_container_width=True):
                for s_ in scenes:
                    s_["effect"] = sc["effect"]
                st.rerun()
            if e2.button("यह बदलाव सब पर", key=f"trall_{sel}", use_container_width=True):
                for s_ in scenes:
                    s_["transition"] = sc["transition"]
                st.rerun()

            if st.button("▶️ इस दृश्य की झलक देखें", key=f"prev_{sel}", type="primary",
                         use_container_width=True):
                try:
                    _, fast = sizes()
                    with st.spinner("झलक बन रही है..."):
                        vid_p = preview_clip(
                            P, sel, fast, S["effect_choice"], S["transition_choice"],
                            S["captions_on"], CAPTION_SIZES[S["cap_name"]],
                        )
                    st.video(vid_p)
                except Exception as e:
                    st.error(f"झलक नहीं बन सकी: {str(e)[-300:]}")
            st.caption("'सेटिंग वाला' का मतलब: ⚙️ सेटिंग में सब दृश्यों के लिए जो चुना है वही।")

        # ============ टाइटल
        elif tab == "🔠 टाइटल":
            sc["title"] = st.text_input(
                "इस दृश्य के ऊपर लिखा शीर्षक (खाली = कोई नहीं)", value=sc.get("title", ""),
                key=f"title_{sel}", placeholder="जैसे: हम्मुराबी की कानून संहिता",
            )
            t1, t2 = st.columns(2)
            ts_keys = list(TITLE_STYLES.keys())
            sc["title_style"] = t1.selectbox(
                "आने का तरीका", ts_keys,
                index=ts_keys.index(sc.get("title_style", "pop")),
                format_func=lambda k: TITLE_STYLES[k], key=f"tstyle_{sel}",
            )
            tp_keys = list(TITLE_POS.keys())
            sc["title_pos"] = t2.selectbox(
                "जगह", tp_keys, index=tp_keys.index(sc.get("title_pos", "top")),
                format_func=lambda k: TITLE_POS[k], key=f"tpos_{sel}",
            )
            st.caption("शीर्षक दृश्य के शुरू के लगभग 3 सेकंड दिखता है।")
            if st.button("▶️ झलक देखें", key=f"tprev_{sel}", use_container_width=True):
                try:
                    _, fast = sizes()
                    with st.spinner("झलक बन रही है..."):
                        vid_p = preview_clip(
                            P, sel, fast, S["effect_choice"], S["transition_choice"],
                            S["captions_on"], CAPTION_SIZES[S["cap_name"]],
                        )
                    st.video(vid_p)
                except Exception as e:
                    st.error(f"झलक नहीं बन सकी: {str(e)[-300:]}")

        # ============ शब्द / आवाज़
        elif tab == "📝 शब्द/आवाज़":
            new_text = st.text_area("बोले जाने वाले शब्द", sc["text"], key=f"t_{sel}", height=130)
            if new_text.strip() and new_text.strip() != sc["text"]:
                if st.button("🎙️ नई आवाज़ बनाएँ", key=f"v_{sel}", type="primary"):
                    try:
                        ff = imageio_ffmpeg.get_ffmpeg_exe()
                        vp = os.path.join(P["folder"], f"voice_edit_{sel}_{int(time.time())}.mp3")
                        rate, pitch = voice_params(new_text, P["speed"], P["natural"])
                        with st.spinner("आवाज़ बन रही है..."):
                            bounds = asyncio.run(
                                synth(new_text.strip(), P["voice"], vp, rate, pitch)
                            )
                        vlen = audio_duration(vp, ff)
                        sc.update(
                            text=new_text.strip(), voice_path=vp, voice_len=vlen,
                            cues=build_cues(new_text.strip(), bounds, vlen),
                        )
                        st.rerun()
                    except Exception as e:
                        st.error(f"आवाज़ नहीं बन सकी: {e}")
            st.caption("शब्द बदलने पर नई आवाज़ और कैप्शन दोनों बनते हैं।")

        # ============ दृश्य (क्रम, दोहराना, जोड़ना, हटाना)
        else:
            m1, m2 = st.columns(2)
            if m1.button("⬆️ ऊपर ले जाएँ", key=f"up_{sel}", disabled=sel == 0,
                         use_container_width=True):
                scenes[sel - 1], scenes[sel] = scenes[sel], scenes[sel - 1]
                st.session_state["_sel_next"] = sel - 1
                st.rerun()
            if m2.button("⬇️ नीचे ले जाएँ", key=f"dn_{sel}", disabled=sel >= n - 1,
                         use_container_width=True):
                scenes[sel + 1], scenes[sel] = scenes[sel], scenes[sel + 1]
                st.session_state["_sel_next"] = sel + 1
                st.rerun()
            if st.button("⧉ इस दृश्य की कॉपी बनाएँ", key=f"dup_{sel}", use_container_width=True):
                dup = copy.deepcopy(sc)
                dup["history"] = []
                scenes.insert(sel + 1, dup)
                st.session_state["_sel_next"] = sel + 1
                st.rerun()

            with st.expander("➕ इसके बाद नया दृश्य जोड़ें"):
                add_text = st.text_area(
                    "बोले जाने वाले शब्द (खाली = 3 सेकंड बिना आवाज़)", key=f"addt_{sel}", height=90
                )
                add_up = st.file_uploader("फोटो (वैकल्पिक, बाद में भी बदल सकते हैं)", key=f"addu_{sel}")
                if st.button("➕ दृश्य जोड़ें", key=f"add_{sel}", type="primary"):
                    try:
                        if add_up is not None:
                            imgp = os.path.join(P["folder"], f"new_{int(time.time() * 1000) % 10**9}.jpg")
                            with Image.open(add_up) as img:
                                rgb = to_rgb(img)
                            rgb.thumbnail((2400, 2400))
                            rgb.save(imgp, "JPEG", quality=92)
                            src_ = "आपकी तस्वीर"
                        else:
                            imgp = os.path.join(P["folder"], f"new_{int(time.time() * 1000) % 10**9}.jpg")
                            make_fallback_image(imgp, P["size"], [], None, title=P.get("anchor", ""))
                            src_ = "खाली (नई फोटो लगाएँ)"
                        with st.spinner("दृश्य बन रहा है..."):
                            ns_ = new_scene(P, add_text, imgp, source=src_)
                        scenes.insert(sel + 1, ns_)
                        st.session_state["_sel_next"] = sel + 1
                        st.rerun()
                    except Exception as e:
                        st.error(f"दृश्य नहीं जुड़ा: {str(e)[-300:]}")

            with st.expander("🗑️ यह दृश्य हमेशा के लिए हटाएँ"):
                sure = st.checkbox("हाँ, इस दृश्य को हटाना है", key=f"sure_{sel}")
                if st.button("🗑️ हटाएँ", key=f"delsc_{sel}", disabled=(not sure) or n <= 1):
                    scenes.pop(sel)
                    st.session_state["_sel_next"] = max(0, sel - 1)
                    st.rerun()
                if n <= 1:
                    st.caption("आख़िरी दृश्य नहीं हटा सकते।")

        # ============ वीडियो बनाना
        st.divider()
        st.markdown("**🎬 वीडियो तैयार करें**")
        music_file = st.file_uploader("🎵 पृष्ठभूमि संगीत (वैकल्पिक)", key="music_up")
        music_vol = st.slider("संगीत की आवाज़", 0.03, 0.40, 0.12, 0.01, key="music_vol")
        fast_mode = st.checkbox(
            "⚡ तेज़ झलक (480p, जल्दी बनता है; अंतिम वीडियो के लिए इसे बंद रखें)", value=False,
            key="fast_mode",
        )
        rendered = bool(P.get("video")) and os.path.exists(P["video"])
        if st.button(
            "🔄 बदलावों के साथ दोबारा बनाएँ" if rendered else "🎬 वीडियो बनाएँ",
            type="primary", use_container_width=True, disabled=busy_any,
        ):
            music_path = None
            if music_file is not None:
                music_path = os.path.join(
                    P["folder"], "music_" + os.path.basename(music_file.name)
                )
                with open(music_path, "wb") as f:
                    f.write(music_file.getbuffer())
            P.update(state="rendering", progress=0, status="शुरू हो रहा है...",
                     error="", video=None, cancel=False, heartbeat=time.time())
            P["note"] = ""
            save_project(P)
            start_thread(render_job, P, render_cfg(fast=fast_mode, music=music_path, vol=music_vol))
            st.rerun()
        st.caption("जो दृश्य बदले नहीं हैं वे दोबारा नहीं बनते, इसलिए दोबारा बनाना तेज़ रहता है।")

        if any(sc_.get("log") for sc_ in scenes):
            with st.expander("🔍 तस्वीर कैसे चुनी गई (जाँच)"):
                for i, sc_ in enumerate(scenes):
                    if sc_.get("log"):
                        st.markdown(f"**दृश्य {i + 1}** → {sc_.get('source', '')}")
                        st.code("\n".join(sc_["log"]), language=None)

# ================================================================ पन्ना: AI फोटो
elif page == "🤖 AI फोटो":
    st.subheader("🤖 AI फोटो स्टूडियो")
    if P is None:
        st.info("फोटो जोड़ने के लिए पहले कोई प्रोजेक्ट खोलें या नया बनाएँ।")
        st.button("🏠 प्रोजेक्ट देखें", on_click=goto, args=("🏠 होम",))
    elif is_busy(P):
        st.info("इस प्रोजेक्ट का काम चल रहा है। पूरा होने तक रुकें।")
    elif not P.get("draft_done"):
        st.info("पहले ड्राफ़्ट पूरा होने दें।")
    else:
        st.caption(f"प्रोजेक्ट: {P.get('title', '')}")
        ai_text = st.text_area(
            "कैसी फोटो चाहिए? (हिंदी या English में)", key="studio_prompt", height=100,
            placeholder="जैसे: सुबह की धूप में प्राचीन बेबीलोन शहर की ऊँची दीवारें",
        )
        style = st.selectbox(
            "शैली", ["असली फोटो जैसी", "फ़िल्मी (सिनेमाई)", "पेंटिंग", "कार्टून"], key="studio_style"
        )
        style_map = {
            "असली फोटो जैसी": "realistic photograph",
            "फ़िल्मी (सिनेमाई)": "cinematic film still, dramatic lighting",
            "पेंटिंग": "detailed oil painting",
            "कार्टून": "colorful cartoon illustration",
        }
        if st.button("🎨 AI से फोटो बनाएँ", type="primary", use_container_width=True):
            prompt = ai_text.strip()
            if not prompt:
                st.warning("पहले लिखें कि कैसी फोटो चाहिए।")
            else:
                if re.search(r"[\u0900-\u097F]", prompt):
                    prompt = translate_hi_en(prompt) or prompt
                prompt = f"{prompt}, {style_map[style]}"
                errs = []
                newp = os.path.join(P["folder"], f"ai_lib_{int(time.time() * 1000) % 10**9}.jpg")
                with st.spinner("AI फोटो बना रहा है (30-90 सेकंड लग सकते हैं)..."):
                    good = ai_image(prompt, newp, P["size"], errs)
                if good:
                    add_to_library(newp, "AI: " + ai_text.strip()[:40])
                    st.success("फोटो बन गई और लाइब्रेरी में जुड़ गई।")
                    st.image(newp, use_container_width=True)
                else:
                    st.error("AI फोटो नहीं बन सकी। थोड़ी देर बाद दोबारा कोशिश करें।")
                    if errs:
                        st.code("\n".join(sorted(set(errs))), language=None)

        st.markdown("**📱 अपनी फोटो लाइब्रेरी में जोड़ें**")
        lib_up = st.file_uploader("फोटो चुनें", accept_multiple_files=True, key="lib_up")
        if lib_up:
            seen_sigs = P.setdefault("lib_sigs", [])
            added = 0
            for up in lib_up:
                sig = f"{up.name}-{up.size}"
                if sig in seen_sigs:
                    continue
                try:
                    p_ = os.path.join(P["folder"], f"libup_{int(time.time() * 1000) % 10**9}_{added}.jpg")
                    with Image.open(up) as img:
                        rgb = to_rgb(img)
                    rgb.thumbnail((2400, 2400))
                    rgb.save(p_, "JPEG", quality=92)
                    add_to_library(p_, "आपकी फोटो: " + up.name[:24])
                    seen_sigs.append(sig)
                    added += 1
                except Exception:
                    st.warning(f"{up.name} तस्वीर के रूप में नहीं खुली।")
            if added:
                st.rerun()

        lib = [e for e in P.get("library", []) if os.path.exists(e["path"])]
        st.markdown(f"**📚 लाइब्रेरी ({len(lib)})**")
        if not lib:
            st.caption("अभी कोई फोटो नहीं है।")
        else:
            for k in range(0, len(lib), 3):
                cols = st.columns(3)
                for j in range(3):
                    idx = k + j
                    if idx >= len(lib):
                        break
                    with cols[j]:
                        st.image(get_thumb(lib[idx]["path"]), use_container_width=True)
                        st.caption(f"{idx + 1}. {lib[idx]['label'][:22]}")

            st.markdown("**वीडियो में लगाएँ**")
            pick = st.selectbox(
                "कौन-सी फोटो?", list(range(len(lib))),
                format_func=lambda i: f"{i + 1}. {lib[i]['label'][:40]}", key="studio_pick",
            )
            n_sc = len(P["scenes"])
            target = st.selectbox(
                "किस दृश्य में?", list(range(n_sc)),
                format_func=lambda i: f"{i + 1}. {P['scenes'][i]['text'][:34]}", key="studio_target",
            )
            g1, g2 = st.columns(2)
            if g1.button("✅ इस दृश्य में लगाएँ", use_container_width=True):
                apply_image(target, lib[pick]["path"], "लाइब्रेरी", lib[pick]["label"][:60])
                st.success(f"दृश्य {target + 1} में फोटो लग गई।")
            with st.expander("➕ इस फोटो से नया दृश्य जोड़ें"):
                new_txt = st.text_area(
                    "बोले जाने वाले शब्द (खाली = 3 सेकंड बिना आवाज़)", key="studio_newtext", height=80
                )
                if st.button("➕ दृश्य जोड़ें", key="studio_add", type="primary"):
                    try:
                        imgp = os.path.join(P["folder"], f"new_{int(time.time() * 1000) % 10**9}.jpg")
                        shutil.copy(lib[pick]["path"], imgp)
                        with st.spinner("दृश्य बन रहा है..."):
                            ns_ = new_scene(P, new_txt, imgp, source="लाइब्रेरी",
                                            query=lib[pick]["label"][:60])
                        P["scenes"].insert(target + 1, ns_)
                        save_project(P)
                        st.success(f"दृश्य {target + 2} के रूप में जुड़ गया। एडिटर में देखें।")
                    except Exception as e:
                        st.error(f"दृश्य नहीं जुड़ा: {str(e)[-300:]}")
            st.button("🎞️ एडिटर खोलें", on_click=goto, args=("🎞️ एडिटर",))

# ================================================================ पन्ना: सेटिंग
else:
    st.subheader("⚙️ सेटिंग")

    st.markdown("**🎞️ वीडियो का रूप**")
    S["effect_choice"] = st.selectbox(
        "सब दृश्यों का कैमरा हलचल", ["Auto (हर दृश्य में अलग)"] + EFFECTS,
        index=(["Auto (हर दृश्य में अलग)"] + EFFECTS).index(S["effect_choice"])
        if S["effect_choice"] in (["Auto (हर दृश्य में अलग)"] + EFFECTS) else 0,
        key="s_eff",
    )
    S["transition_choice"] = st.selectbox(
        "सब दृश्यों का बदलाव (ट्रांज़िशन)", ["Auto (हर दृश्य अलग)"] + TRANSITIONS,
        index=(["Auto (हर दृश्य अलग)"] + TRANSITIONS).index(S["transition_choice"])
        if S["transition_choice"] in (["Auto (हर दृश्य अलग)"] + TRANSITIONS) else 0,
        format_func=lambda k: k if k.startswith("Auto") else TRANS_NAMES.get(k, k),
        key="s_tr",
    )
    S["captions_on"] = st.checkbox(
        "💬 हिंदी कैप्शन (बोले जा रहे शब्द)", value=S["captions_on"], key="s_cap"
    )
    S["cap_name"] = st.select_slider(
        "कैप्शन का आकार", list(CAPTION_SIZES.keys()), value=S["cap_name"], key="s_capsize"
    )

    st.markdown("**🔑 चाबियाँ (मुफ़्त)**")
    S["gemini_key"] = st.text_input(
        "Google Gemini API key (aistudio.google.com से)", value=S["gemini_key"],
        type="password", placeholder="खाली छोड़ें अगर Secrets में सेट है", key="s_gem",
    )
    if not S["gemini_key"].strip():
        if secret("GEMINI_API_KEY"):
            st.caption("✅ Gemini key पहले से सेट है (Secrets से), यह खाना खाली रहना सामान्य है।")
        else:
            st.caption(
                "⚠️ Secrets में GEMINI_API_KEY नहीं मिली। Secrets में मिले नाम: "
                f"{', '.join(secret_names()) or 'कोई नहीं'}।"
            )
    if st.button("🔑 key जाँचें"):
        if not eff_gemini():
            st.warning("पहले key चिपकाएँ या Secrets में रखें।")
        else:
            ok, msg = test_gemini(eff_gemini())
            (st.success if ok else st.error)(msg)

    S["pixabay_key"] = st.text_input(
        "Pixabay API key (वैकल्पिक)", value=S["pixabay_key"], type="password", key="s_pix"
    )
    if not S["pixabay_key"].strip() and secret("PIXABAY_API_KEY"):
        st.caption("✅ Pixabay key पहले से सेट है (Secrets से)।")

    if st.button("🧪 सेवाएँ जाँचें (तस्वीर/AI क्यों नहीं मिल रही)"):
        with st.spinner("हर सेवा जाँची जा रही है... (1-2 मिनट लग सकते हैं)"):
            rows = diagnose_services(eff_pixabay())
        for name_, good_, detail_ in rows:
            (st.success if good_ else st.error)(f"{name_}: {detail_}")

    st.markdown("**🧹 सफ़ाई**")
    if st.button("पुराने सब प्रोजेक्ट हटाएँ (चल रहा काम छोड़कर)", disabled=busy_any):
        for _id in list(JOBS.keys()):
            if not is_busy(JOBS[_id]):
                delete_project(_id)
        st.rerun()

    st.caption(APP_VERSION)

# ---------------------------------------------------------------- काम चलते समय पन्ना अपने आप ताज़ा हो
if busy_any:
    time.sleep(3)
    st.rerun()
