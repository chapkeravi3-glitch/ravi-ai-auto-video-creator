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
HEADERS = {"User-Agent": "RaviAIVideoCreator/6.0 (personal video project)"}
APP_VERSION = "संस्करण 6 (26 अक्टूबर 2026)"

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
              pad, is_last):
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

            log, ai_err = [], []
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
                anchor, exclude=exclude, modern=modern, log=log,
            )

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


def start_thread(fn, P, cfg):
    t = threading.Thread(target=fn, args=(P, cfg), daemon=True)
    t.start()



# ==== UI ====
st.title("🎬 Ravi AI Auto Video Creator")
st.caption(APP_VERSION)


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


pid = st.query_params.get("p")
P = JOBS.get(pid) if pid else None
if P is None and JOBS and not st.session_state.get("fresh"):
    # लिंक में पहचान न हो (जैसे ऐप दोबारा खोला) तो आख़िरी प्रोजेक्ट खोलें
    P = max(JOBS.values(), key=lambda p: p.get("created", 0))
    st.query_params["p"] = P["id"]

# अटका हुआ काम पहचानें (15 मिनट से कोई हलचल नहीं)
if P and P["state"] in ("drafting", "rendering"):
    if time.time() - P.get("heartbeat", P.get("created", time.time())) > 900:
        P["state"] = "error"
        P["error"] = "काम बहुत देर से अटका हुआ था, इसलिए रोक दिया गया।"

busy = bool(P) and P["state"] in ("drafting", "rendering")

# ---------------------------------------------------------------- कहानी और सेटिंग
with st.expander("📝 कहानी और सेटिंग", expanded=(P is None)):
    script = st.text_area("पूरी हिंदी कहानी लिखें", height=240)

    voice_name = st.radio("🎙️ आवाज़", list(VOICES.keys()), horizontal=True)
    speed = st.slider("बोलने की गति", -25, 15, -5, 1, help="ऋण का मतलब थोड़ा धीरे")
    natural = st.checkbox(
        "🗣️ प्राकृतिक आवाज़ (हर दृश्य में थोड़ी अलग गति/सुर)", value=True
    )
    if st.button("🔊 चुनी हुई आवाज़ सुनें"):
        try:
            sample = os.path.join(
                tempfile.gettempdir(), f"ravi_sample_{int(time.time())}.mp3"
            )
            asyncio.run(synth(
                "नमस्ते दोस्तो, आज हम एक बहुत रोचक कहानी सुनेंगे।",
                VOICES[voice_name], sample, f"{int(speed):+d}%", "+0Hz",
            ))
            with open(sample, "rb") as f:
                st.audio(f.read(), format="audio/mp3")
        except Exception as e:
            st.error(f"आवाज़ का नमूना नहीं बन सका: {e}")

    c1, c2 = st.columns(2)
    with c1:
        format_name = st.selectbox("📐 वीडियो का आकार", list(FORMATS.keys()))
        quality = st.selectbox(
            "🎞️ वीडियो क्वालिटी",
            ["HD 1080p (बेहतर, थोड़ा धीमा)", "सामान्य 720p (तेज़)"],
        )
    with c2:
        effect_choice = st.selectbox(
            "✨ सब दृश्यों का इफेक्ट", ["Auto (हर दृश्य में अलग)"] + EFFECTS
        )
        cap_name = st.select_slider(
            "कैप्शन का आकार", list(CAPTION_SIZES.keys()), value="मध्यम"
        )

    max_chars = st.slider(
        "✂️ एक दृश्य में अधिकतम अक्षर (कम = तस्वीरें जल्दी-जल्दी बदलेंगी)",
        60, 400, 110, 10,
    )
    captions_on = st.checkbox("💬 हिंदी कैप्शन (बोले जा रहे शब्द स्क्रीन पर)", value=True)
    use_web = st.checkbox(
        "🌐 इंटरनेट की तस्वीरों में खोजें (कॉपीराइट आपकी ज़िम्मेदारी है)", value=True
    )
    use_ai = st.checkbox("🤖 मेल खाती तस्वीर न मिले तो AI से बनाएँ", value=True)

    gemini_key = st.text_input(
        "Google Gemini API key (मुफ़्त; aistudio.google.com से; सबसे सटीक तस्वीरों के लिए)",
        type="password",
        placeholder="खाली छोड़ें अगर Secrets में सेट है",
    )
    if not gemini_key.strip():
        if secret("GEMINI_API_KEY"):
            gemini_key = secret("GEMINI_API_KEY")
            st.caption("✅ Gemini key पहले से सेट है (Secrets से), यह खाना खाली रहना सामान्य है।")
        else:
            names = ", ".join(secret_names()) or "कोई नहीं"
            st.caption(
                "⚠️ Secrets में GEMINI_API_KEY नहीं मिली। Secrets में मिले नाम: "
                f"{names}। नाम बिल्कुल GEMINI_API_KEY होना चाहिए और key सीधे "
                "उद्धरण चिह्न \" \" के बीच हो।"
            )
    if st.button("🔑 key जाँचें"):
        if not gemini_key.strip():
            st.warning("पहले key चिपकाएँ या Secrets में रखें।")
        else:
            ok, msg = test_gemini(gemini_key.strip())
            (st.success if ok else st.error)(msg)

    extra_topic = st.text_input(
        "कहानी का मुख्य विषय English में (वैकल्पिक, हर खोज में जुड़ेगा)",
        placeholder="India vs Australia ODI 2026",
    )
    pixabay_key = st.text_input("Pixabay API key (वैकल्पिक, मुफ़्त)", type="password")
    if not pixabay_key.strip() and secret("PIXABAY_API_KEY"):
        pixabay_key = secret("PIXABAY_API_KEY")
        st.caption("✅ Pixabay key पहले से सेट है (Secrets से)।")

    if st.button("🧪 सेवाएँ जाँचें (तस्वीर/AI क्यों नहीं मिल रही)"):
        with st.spinner("हर सेवा जाँची जा रही है... (1-2 मिनट लग सकते हैं)"):
            rows = diagnose_services(pixabay_key.strip())
        for name, good, detail in rows:
            (st.success if good else st.error)(f"{name}: {detail}")

    uploaded_images = st.file_uploader(
        "🖼️ अपनी तस्वीरें (पहले दृश्यों में क्रम से लगेंगी)",
        accept_multiple_files=True,
    )
    if uploaded_images:
        st.caption(f"✅ {len(uploaded_images)} फ़ाइल मिली")

    start = st.button("🎬 नया वीडियो बनाना शुरू करें", type="primary", disabled=busy)

base_size = FORMATS[format_name]
render_size = (
    (base_size[0] * 3 // 2, base_size[1] * 3 // 2)
    if quality.startswith("HD") else base_size
)


def make_cfg(resume=False, uploads=None):
    return {
        "resume": resume,
        "voice": VOICES[voice_name], "speed": speed, "natural": natural,
        "max_chars": max_chars, "size": render_size,
        "use_web": use_web, "use_ai": use_ai,
        "gemini_key": gemini_key.strip(), "pixabay_key": pixabay_key.strip(),
        "extra_topic": extra_topic, "uploads": uploads or [],
        "render": {
            "size": render_size, "default_effect": effect_choice,
            "default_transition": "Auto (हर दृश्य अलग)",
            "captions": captions_on, "cap_ratio": CAPTION_SIZES[cap_name],
            "music": None, "music_vol": 0.1,
        },
    }


# ---------------------------------------------------------------- चल रहा काम
if busy:
    st.progress(min(100, P["progress"]))
    st.info(P["status"])
    st.caption(
        "💡 काम सर्वर पर चलता है। फ़ोन की स्क्रीन बंद हो जाए तो भी चलता रहेगा। "
        "ऐप दोबारा खोलने पर यहीं से आगे दिखेगा। रुक जाए तो बना हुआ काम दोबारा नहीं बनेगा।"
    )
    if st.button("⏹️ रोकें"):
        P["cancel"] = True
        P["status"] = "रोका जा रहा है..."
    time.sleep(3)
    st.rerun()


# ---------------------------------------------------------------- एडिटर
def set_scene_image(i, newp, source, query):
    sc = P["scenes"][i]
    sc.setdefault("history", []).append((sc["image"], sc["source"], sc["query"]))
    sc.update(image=newp, source=source, query=query)


def editor():
    scenes = P["scenes"]
    n = len(scenes)
    st.subheader("✏️ एडिटर")
    st.caption("नीचे हर दृश्य की झलक है। जिसे बदलना हो उसे नीचे से चुनें, फिर बदलाव करके वीडियो दोबारा बनाएँ।")

    if "sel" not in st.session_state or st.session_state["sel"] >= n:
        st.session_state["sel"] = 0

    # टाइमलाइन
    for r in range(0, n, 4):
        cols = st.columns(4)
        for c in range(4):
            i = r + c
            if i >= n:
                break
            with cols[c]:
                sc = scenes[i]
                if sc.get("image") and os.path.exists(sc["image"]):
                    st.image(get_thumb(sc["image"]), use_container_width=True)
                mark = "🚫 " if not sc["keep"] else ("▶ " if i == st.session_state["sel"] else "")
                st.caption(f"{mark}{i + 1}")

    def _prev():
        st.session_state["sel"] = max(0, st.session_state["sel"] - 1)

    def _next():
        st.session_state["sel"] = min(n - 1, st.session_state["sel"] + 1)

    b1, b2 = st.columns(2)
    b1.button("◀ पिछला दृश्य", on_click=_prev, key="prev_btn", use_container_width=True)
    b2.button("अगला दृश्य ▶", on_click=_next, key="next_btn", use_container_width=True)

    sel = st.selectbox(
        "बदलने के लिए दृश्य चुनें",
        list(range(n)),
        key="sel",
        format_func=lambda i: f"{i + 1}. {scenes[i]['text'][:34]}",
    )
    sc = scenes[sel]

    if sc.get("image") and os.path.exists(sc["image"]):
        st.image(sc["image"], use_container_width=True)
    st.caption(f"स्रोत: {sc['source']} | {sc['query'][:70]}")
    st.write(sc["text"])

    t1, t2, t3, t4 = st.tabs(["🖼️ तस्वीर", "✨ इफेक्ट", "🎙️ शब्द/आवाज़", "🎵 संगीत"])

    # --- तस्वीर बदलना
    with t1:
        q = st.text_input(
            "नई तस्वीर के लिए English शब्द (जितने सटीक, उतना अच्छा)",
            value=sc["query"] if sc["source"] not in ("AI", "खाली", "रंगीन पृष्ठभूमि", "पहले की तस्वीर") else "",
            key=f"q_{sel}",
            placeholder="Ishtar Gate Babylon",
        )
        ca, cb = st.columns(2)
        if ca.button("🔍 इंटरनेट से खोजें", key=f"s_{sel}", use_container_width=True):
            newp = os.path.join(P["folder"], f"scene_{sel}_{int(time.time())}.jpg")
            log = []
            with st.spinner("खोज रहे हैं..."):
                src, qq = find_photo(
                    [q], newp, P["used"], pixabay_key.strip(), use_web, "",
                    modern=P.get("modern", False), log=log,
                )
            if src:
                set_scene_image(sel, newp, src, qq)
                st.rerun()
            else:
                st.warning("मेल खाती तस्वीर नहीं मिली। दूसरे शब्द आज़माएँ।")
                st.code("\n".join(log) or "कोई स्रोत नहीं चला", language=None)
        if cb.button("🤖 AI से बनाएँ", key=f"a_{sel}", use_container_width=True):
            newp = os.path.join(P["folder"], f"scene_{sel}_{int(time.time())}.jpg")
            prompt = q if len(q.strip()) >= 15 else visual_prompt(
                sc["text"], P.get("topic", ""), P.get("anchor", "")
            )
            errs = []
            with st.spinner("AI तस्वीर बना रहा है (30-90 सेकंड लग सकते हैं)..."):
                good = bool(prompt) and ai_image(prompt, newp, P["size"], errs)
            if good:
                set_scene_image(sel, newp, "AI", prompt[:120])
                st.rerun()
            else:
                st.warning("AI तस्वीर नहीं बन सकी।")
                if errs:
                    st.code("\n".join(sorted(set(errs))), language=None)

        up = st.file_uploader(
            "या अपनी तस्वीर लगाएँ", key=f"up_{sel}"
        )
        if up is not None:
            sig = f"{up.name}-{up.size}"
            if sc.get("up_sig") != sig:
                try:
                    newp = os.path.join(P["folder"], f"scene_{sel}_{int(time.time())}.jpg")
                    with Image.open(up) as img:
                        rgb = to_rgb(img)
                    rgb.thumbnail((2400, 2400))
                    rgb.save(newp, "JPEG", quality=92)
                    sc["up_sig"] = sig
                    set_scene_image(sel, newp, "आपकी तस्वीर", "")
                    st.rerun()
                except Exception:
                    st.error("यह फ़ाइल तस्वीर के रूप में नहीं खुली।")

        if sc.get("history"):
            if st.button("↩️ पिछली तस्वीर वापस लाएँ", key=f"undo_{sel}"):
                img_p, src_p, q_p = sc["history"].pop()
                sc.update(image=img_p, source=src_p, query=q_p)
                st.rerun()

    # --- इफेक्ट
    with t2:
        scene_fx = ["Default"] + EFFECTS
        scene_tr = ["Default"] + TRANSITIONS
        sc["keep"] = st.checkbox("इस दृश्य को रखें", sc["keep"], key=f"keep_{sel}")
        sc["effect"] = st.selectbox(
            "ज़ूम/पैन इफेक्ट", scene_fx,
            index=scene_fx.index(sc["effect"]) if sc["effect"] in scene_fx else 0,
            key=f"eff_{sel}",
        )
        sc["transition"] = st.selectbox(
            "इस दृश्य में आते समय ट्रांज़िशन", scene_tr,
            index=scene_tr.index(sc["transition"]) if sc["transition"] in scene_tr else 0,
            key=f"tr_{sel}",
        )
        sc["pad"] = st.slider(
            "दृश्य के अंत में ठहराव (सेकंड)", 0.2, 3.0, float(sc.get("pad", 0.35)), 0.1,
            key=f"pad_{sel}",
        )
        st.caption("'Default' का मतलब: ऊपर 'कहानी और सेटिंग' में जो सब दृश्यों के लिए चुना है वही।")

    # --- शब्द और आवाज़
    with t3:
        new_text = st.text_area("बोले जाने वाले शब्द", sc["text"], key=f"t_{sel}", height=120)
        if new_text.strip() and new_text.strip() != sc["text"]:
            if st.button("🎙️ नई आवाज़ बनाएँ", key=f"v_{sel}"):
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

    # --- संगीत
    with t4:
        music_file = st.file_uploader("🎵 पृष्ठभूमि संगीत (mp3/wav/m4a)", key="music_up")
        music_vol = st.slider("संगीत की आवाज़", 0.03, 0.40, 0.12, 0.01, key="music_vol")
        st.caption("संगीत पूरे वीडियो पर चलेगा और आवाज़ के नीचे धीमा रहेगा।")

    st.divider()
    rendered = bool(P.get("video")) and os.path.exists(P["video"])
    if st.button(
        "🔄 बदलावों के साथ वीडियो दोबारा बनाएँ" if rendered else "🎬 वीडियो बनाएँ",
        type="primary", use_container_width=True,
    ):
        music_path = None
        if music_file is not None:
            music_path = os.path.join(
                P["folder"], "music_" + os.path.basename(music_file.name)
            )
            with open(music_path, "wb") as f:
                f.write(music_file.getbuffer())
        rcfg = dict(make_cfg()["render"], music=music_path, music_vol=music_vol)
        P.update(state="rendering", progress=0, status="शुरू हो रहा है...",
                 error="", video=None, cancel=False, heartbeat=time.time())
        P["note"] = ""
        save_project(P)
        start_thread(render_job, P, rcfg)
        st.rerun()
    st.caption("जो दृश्य बदले नहीं हैं वे दोबारा नहीं बनते, इसलिए दोबारा बनाना तेज़ रहता है।")


# ---------------------------------------------------------------- तैयार वीडियो / अधूरा काम
resume_clicked = False
if P is not None:
    video_ready = bool(P.get("video")) and os.path.exists(P["video"])

    if P.get("note"):
        st.warning(P["note"])
    if P["state"] == "error" and P.get("error"):
        st.error("बीच में समस्या आई।")
        st.code(P["error"][-600:])

    if video_ready:
        st.success("वीडियो तैयार है! नीचे एडिटर से कोई भी बदलाव करके दोबारा बना सकते हैं।")
        st.video(P["video"])

        if P.get("static_name"):
            st.markdown(
                f'<a href="app/static/{P["static_name"]}" download="ravi_video.mp4" '
                'style="display:block;text-align:center;padding:0.7rem 1rem;'
                'border-radius:0.5rem;background:#ff4b4b;color:#fff;'
                'font-weight:600;text-decoration:none;">'
                "⬇️ वीडियो डाउनलोड करें</a>",
                unsafe_allow_html=True,
            )
            st.caption(
                "डाउनलोड के बाद वीडियो खुल जाए और वापस आने का रास्ता न दिखे, "
                "तो इसी ऐप को दोबारा खोलें। आपका वीडियो यहीं सुरक्षित रहेगा।"
            )
        try:
            with open(P["video"], "rb") as f:
                st.download_button(
                    "⬇️ दूसरा तरीका: MP4 डाउनलोड", data=f,
                    file_name="ravi_video.mp4", mime="video/mp4",
                    on_click="ignore",
                )
        except TypeError:
            with open(P["video"], "rb") as f:
                st.download_button(
                    "⬇️ दूसरा तरीका: MP4 डाउनलोड", data=f,
                    file_name="ravi_video.mp4", mime="video/mp4",
                )

    if P.get("draft_done"):
        editor()
    else:
        done_n = sum(1 for sc in P["scenes"] if sc.get("image"))
        if P["scenes"]:
            st.warning(
                f"ड्राफ़्ट अभी पूरा नहीं बना (तस्वीरें {done_n}/{len(P['scenes'])})। "
                "जो बन चुका है वह दोबारा नहीं बनेगा।"
            )
        resume_clicked = st.button("▶️ वहीं से आगे बढ़ाएँ", type="primary")

    if any(sc.get("log") for sc in P["scenes"]):
        with st.expander("🔍 तस्वीर कैसे चुनी गई (जाँच)"):
            for i, sc in enumerate(P["scenes"]):
                if sc.get("log"):
                    st.markdown(f"**दृश्य {i + 1}** → {sc.get('source', '')}")
                    st.code("\n".join(sc["log"]), language=None)

    st.divider()
    if st.button("🆕 बिल्कुल नया वीडियो बनाएँ"):
        st.session_state["fresh"] = True
        st.query_params.clear()
        st.rerun()

# ---------------------------------------------------------------- बटनों की कार्रवाई
if resume_clicked and P is not None:
    P["error"], P["note"], P["cancel"] = "", "", False
    P["heartbeat"] = time.time()
    P["state"] = "drafting" if not P.get("draft_done") else "rendering"
    start_thread(full_job, P, make_cfg(resume=True))
    st.rerun()

if start:
    if not script.strip():
        st.error("पहले कहानी लिखें।")
        st.stop()

    if P and os.path.exists(P.get("folder", "")):
        shutil.rmtree(P["folder"], ignore_errors=True)
        JOBS.pop(P["id"], None)

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
        st.warning(
            "ये फ़ाइलें तस्वीर के रूप में नहीं खुलीं और छोड़ दी गईं: " + ", ".join(bad)
        )

    P = {
        "id": new_id, "folder": folder, "state": "drafting",
        "created": time.time(), "heartbeat": time.time(),
        "script": script, "draft_done": False, "planned": False,
        "status": "शुरू हो रहा है...", "progress": 0,
        "note": "", "error": "", "scenes": [], "used": set(), "video": None,
        "anchor": "", "topic": "", "modern": False, "offset": 0,
        "voice": VOICES[voice_name], "speed": speed, "natural": natural,
        "size": render_size, "cancel": False, "static_name": "",
    }
    JOBS[new_id] = P
    st.session_state["fresh"] = False
    st.session_state.pop("sel", None)
    start_thread(full_job, P, make_cfg(uploads=uploads))
    st.query_params["p"] = new_id
    st.rerun()
