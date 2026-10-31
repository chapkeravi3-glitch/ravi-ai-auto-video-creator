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
import base64
import datetime
import threading
from urllib.parse import quote
from PIL import Image, ImageDraw, ImageFilter, ImageChops, ImageFont, ImageOps, ImageStat
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
# packages.txt (हिंदी कैप्शन/टेक्स्ट कार्ड के फ़ॉन्ट के लिए, Streamlit Cloud पर):
# fonts-noto-core
#
# .streamlit/config.toml (सीधे डाउनलोड लिंक के लिए):
# [server]
# enableStaticServing = true

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

TODAY = datetime.date.today()

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
HEADERS = {"User-Agent": "RaviAIVideoCreator/7.0 (personal video project)"}
APP_VERSION = "संस्करण 7 (31 अक्टूबर 2026)"

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

# फोटो कितनी नई चाहिए: (DuckDuckGo समय-सीमाएँ क्रम से, कितने दिन पीछे तक का साल मान्य)
REC_MODES = {
    "week": (["w", "y"], 370),
    "month": (["m", "y"], 370),
    "year": (["y"], 370),
    "3y": ([None], 3 * 365),
    "any": ([None], None),
}
REC_LABELS = {
    "अपने-आप (AI कहानी देखकर तय करे)": "auto",
    "आज / इस हफ़्ते की": "week",
    "इस महीने की": "month",
    "इस साल की": "year",
    "पिछले 3 साल की": "3y",
    "कोई भी, पुरानी भी चलेगी": "any",
}
REC_TEXT = {
    "week": "taken within the last few days (very recent news photo)",
    "month": "taken within the last month",
    "year": "taken within the last year",
    "3y": "taken within the last 3 years",
    "any": "any date",
}
GENDER_LABELS = {
    "अपने-आप पहचाने": "auto",
    "पुरुष (Men's)": "men",
    "महिला (Women's)": "women",
    "दोनों / कोई फ़र्क नहीं": "none",
}

# ग़लत विषय की फोटो रोकने के लिए: (विषय पहचानने वाले शब्द, उस विषय में ग़लत मानी जाने वाली चीज़ें)
DOMAINS = [
    (("cricket", "क्रिकेट", "odi", "t20", "ipl", "bcci", "wicket"),
     ["wwe", "wrestling", "wrestler", "football", "soccer", "fifa", "nba", "basketball",
      "nfl", "rugby", "tennis", "golf", "hockey", "baseball", "boxing", "ufc", "mma",
      "badminton", "kabaddi", "chess", "formula"]),
    (("football", "soccer", "फुटबॉल", "fifa"),
     ["cricket", "wwe", "wrestling", "nba", "tennis", "golf", "baseball", "rugby", "kabaddi"]),
    (("tennis", "टेनिस"),
     ["cricket", "wwe", "wrestling", "football", "soccer", "nba", "golf", "baseball"]),
    (("wwe", "wrestling", "कुश्ती"),
     ["cricket", "football", "soccer", "nba", "tennis", "golf"]),
]

STOP_HI = set(
    "है हैं था थी थे हो हुआ हुई हुए की के का को में से पर और या कि जो यह वह इस उस ने भी "
    "तो ही एक कर करने किया गया गई गए रहा रही रहे लिए साथ बाद तक".split()
)

# टेक्स्ट कार्ड के रंग: (गहरा, मध्य, चमक) पृष्ठभूमि और (हाइलाइट RGB, ASS रंग)
CARD_BG = [
    ("#0b132b", "#1c2541", "#3a506b"),
    ("#1a0b2e", "#3d1a78", "#6a0572"),
    ("#0f2027", "#203a43", "#2c5364"),
    ("#200122", "#6f0000", "#a4133c"),
    ("#04293a", "#064663", "#0b7a75"),
]
CARD_ACCENT = [
    ("#FFD700", "&H0000D7FF&"),
    ("#00E5FF", "&H00FFE500&"),
    ("#FF4081", "&H008140FF&"),
    ("#69F0AE", "&H00AEF069&"),
    ("#FF9100", "&H000091FF&"),
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


def auto_headline(text, n=5):
    """दृश्य के अपने शब्दों से बड़े अक्षरों वाला छोटा वाक्यांश (फोटो न मिलने पर दिखेगा)।"""
    text = (text or "").strip()
    first = re.split(r"[।!?\.,;:—\n]", text)[0] if text else ""
    ws = [w.strip("\"'“”‘’()[]") for w in first.split()]
    ws = [w for w in ws if w and w not in STOP_HI]
    if len(ws) < 2:
        ws = [w for w in re.sub(r"[।!?\.,;:—]", " ", text).split() if w not in STOP_HI]
    return " ".join(ws[:n])


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


def has_word(text, w):
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", text))


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


def guess_modern(*texts):
    blob = " ".join(texts).lower()
    if re.search(r"\b(20[1-3]\d)\b", blob):
        return True
    return bool(re.search(
        r"cricket|क्रिकेट|odi\b|t20|ipl|world cup|match|मैच|tournament|league|"
        r"election|चुनाव|smartphone|startup",
        blob,
    ))


def guess_gender(blob):
    b = (blob or "").lower()
    w = bool(re.search(r"महिला|women|ladies|female|girls|लड़कियाँ|लड़कियां", b))
    m = bool(re.search(r"पुरुष|men's|\bmen\b|gents|\bmale\b", b))
    if w and not m:
        return "women"
    if m and not w:
        return "men"
    return "none"


def domain_excludes(blob):
    b = (blob or "").lower()
    out, own = set(), set()
    for trig, ex in DOMAINS:
        if any(has_word(b, t) for t in trig):
            out |= set(ex)
            own |= set(trig)
    return sorted(out - own)


# ---------------------------------------------------------------- Gemini
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


def gemini_generate(key, prompt, models, parts=None):
    """prompt (सादा टेक्स्ट) या parts (टेक्स्ट + तस्वीरें) भेजकर जवाब लाता है।"""
    errors = []
    url = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
    for model in models:
        for use_json in (True, False):
            try:
                body = {"contents": [{"parts": parts or [{"text": prompt}]}]}
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
                        ps = cands[0].get("content", {}).get("parts", [])
                        text = "".join(p.get("text", "") for p in ps)
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


def normalize_ctx(d):
    d = d if isinstance(d, dict) else {}
    g = str(d.get("gender", "")).lower()
    g = g if g in ("men", "women") else "none"
    rec = str(d.get("recency", "")).lower()
    rec = rec if rec in REC_MODES else ""
    era = "historical" if str(d.get("era", "")).lower().startswith("hist") else "modern"

    def words(v, n):
        out = []
        for x in (v if isinstance(v, list) else []):
            if isinstance(x, str):
                x = x.strip().lower()
                if len(x) >= 2 and x not in out:
                    out.append(x)
        return out[:n]

    return {
        "topic_en": str(d.get("topic_en", "")).strip()[:300],
        "anchor": str(d.get("anchor", "")).strip()[:60],
        "era": era, "gender": g, "recency": rec,
        "must_any": words(d.get("must_any"), 8),
        "off_topic": words(d.get("off_topic"), 14),
    }


def analyze_story(script, extra, key, models):
    """पूरी कहानी एक बार पढ़कर: विषय, दौर, पुरुष/महिला, कितनी नई फोटो, ज़रूरी/ग़लत शब्द।"""
    prompt = (
        "Today's date is " + TODAY.isoformat() + ".\n"
        "Analyse this Hindi story to choose matching REAL photographs. Reply with ONLY JSON:\n"
        '{"topic_en":"one English sentence: what the story is about",'
        '"anchor":"1-3 English words naming the main subject, e.g. India women cricket",'
        '"era":"modern" or "historical",'
        '"gender":"men" or "women" or "none" (are the people/teams in the story men or women; '
        '"none" if mixed or not about people),'
        '"recency":"week" or "month" or "year" or "3y" or "any" (how recent the photos must be: '
        '"week" if it is about today\'s or this week\'s event, "any" for history),'
        '"must_any":[3 to 8 lowercase English words such as team, sport, person and place names; '
        'a correct photo caption normally contains at least one],'
        '"off_topic":[up to 12 lowercase English words that signal a WRONG photo for this story, '
        "such as other sports or unrelated celebrities]}\n"
        + (f"Main subject given by the user: {extra}\n" if extra else "")
        + "Story:\n" + script[:2000]
    )
    text, err = gemini_generate(key, prompt, models)
    return (parse_json(text) if text else None), err


PLAN_PROMPT = (
    "You choose real photographs for a Hindi video. Today's date: {today}.\n"
    "Story: {topic}\nMain subject: {anchor}. People in the story: {gender}. Era: {era}.\n"
    "Scenes (Hindi), numbered:\n{listing}\n\n"
    "For EACH scene return:\n"
    '- "queries": 2 English image-search queries (2-6 words) for a REAL photograph showing '
    "what this scene says. First = most specific (team/person/place names + what is happening"
    "{yearhint}), second = safer and broader. Always keep the main subject name; never only "
    "generic words.\n"
    '- "ai": ONE detailed English sentence describing the photo to generate if no real photo '
    "exists: same subject, same men/women, setting, lighting. No text in the image.\n"
    '- "exclude": 0 to 5 English words that would show a WRONG photo for this scene.\n'
    '- "headline": 2 to 5 words in Devanagari copied or shortened from the scene itself, the '
    "key phrase to show as big text if no photo is found.\n"
    'Respond with ONLY JSON: {"scenes":[{"n":1,"queries":["...","..."],"ai":"...",'
    '"exclude":["..."],"headline":"..."}]}'
)


def plan_with_ai(scenes, P, key, models, on_progress=None):
    ctx = P.get("ctx") or {}
    g = {"men": "men (male)", "women": "women (female)"}.get(ctx.get("gender"), "mixed or not specified")
    plans = [None] * len(scenes)
    last_error = ""
    CH = 12
    for start in range(0, len(scenes), CH):
        chunk = scenes[start:start + CH]
        listing = "\n".join(f"{start + j + 1}. {s[:300]}" for j, s in enumerate(chunk))
        yearhint = f", add the year {TODAY.year}" if P.get("rec") in ("week", "month", "year") else ""
        prompt = (
            PLAN_PROMPT.replace("{today}", TODAY.isoformat())
            .replace("{topic}", P.get("topic", ""))
            .replace("{anchor}", P.get("anchor", ""))
            .replace("{gender}", g)
            .replace("{era}", ctx.get("era", "modern"))
            .replace("{yearhint}", yearhint)
            .replace("{listing}", listing)
        )
        data = None
        for _ in range(2):
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
        for item in data.get("scenes", []):
            try:
                n = int(item.get("n")) - 1
            except Exception:
                continue
            if 0 <= n < len(scenes):
                plans[n] = item
    return plans, last_error


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
    if anchor and anchor.lower() not in query.lower() and len(query.split()) < 4:
        return f"{anchor} {query}"
    return query


def visual_prompt(scene, topic, anchor, english=""):
    """दृश्य की कथा से एक ठोस English तस्वीर-विवरण (बिना Gemini के AI तस्वीर के लिए)।"""
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


GENDER_W = re.compile(r"\b(women|women's|womens|woman|female|ladies|girls?)\b", re.I)
GENDER_M = re.compile(r"\b(men|men's|mens|man|male|boys?|gents)\b", re.I)
YEAR_RE = re.compile(r"(?<!\d)(19\d\d|20\d\d)(?!\d)")
DATE8_RE = re.compile(r"(?<!\d)(20\d\d)(?=(?:0[1-9]|1[0-2])[0-3]\d(?!\d))")


def gender_ok(text, gender):
    """पुरुषों की कहानी में महिला वाली और महिलाओं की कहानी में पुरुष वाली फोटो नहीं।"""
    t = (text or "").replace("’", "'")
    w, m = bool(GENDER_W.search(t)), bool(GENDER_M.search(t))
    if gender == "women":
        return not (m and not w)
    if gender == "men":
        return not w
    return True


def years_in(text):
    t = re.sub(r"\d{3,4}\s*[x×]\s*\d{3,4}", " ", text or "")
    ys = [int(y) for y in YEAR_RE.findall(t)] + [int(y) for y in DATE8_RE.findall(t)]
    return [y for y in ys if 2005 <= y <= TODAY.year + 1]


def ahash(img):
    g = img.convert("L").resize((8, 8), Image.LANCZOS)
    px = list(g.getdata())
    avg = sum(px) / 64
    return sum(1 << i for i, p in enumerate(px) if p >= avg)


def is_dup(h, hashes, thr=5):
    return any(bin(h ^ x).count("1") <= thr for x in hashes)


# ---------------------------------------------------------------- तस्वीर के स्रोत
# हर स्रोत (url, शीर्षक/टैग, साल या None) की सूची देता है, ताकि मेल और तारीख जाँची जा सके।
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
            out.append((url, h.get("tags", ""), None))
        return out
    except Exception as e:
        _note(err, f"Pixabay: {str(e)[:90]}")
        return []


def web_urls(query, err=None, timelimit=None):
    if DDGS is None:
        _note(err, "इंटरनेट खोज की लाइब्रेरी (ddgs) इंस्टॉल नहीं है")
        return []
    try:
        kw = {"max_results": 25}
        if timelimit:
            kw["timelimit"] = timelimit
        with DDGS() as d:
            results = list(d.images(query, **kw))
        out = []
        for r in results:
            page = (r.get("url") or "").split("?")[0]
            text = f"{r.get('title', '')} {page} {r.get('source', '')}"
            out.append((r.get("image"), text, None))
        return out
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
            yr = None
            try:
                yr = int(str(item.get("created_on") or "")[:4])
            except Exception:
                pass
            out.append((item.get("url") or item.get("thumbnail"), text, yr))
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
                dt = ((meta.get("DateTimeOriginal") or {}).get("value", "")
                      or (meta.get("DateTime") or {}).get("value", ""))
                ys = [int(y) for y in YEAR_RE.findall(dt)]
                out.append((url, f"{page.get('title', '')} {desc}", ys[0] if ys else None))
            return out
        except Exception as e:
            _note(err, f"Wikimedia: {str(e)[:90]}")
    return []


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


# ---------------------------------------------------------------- स्मार्ट चुनाव
def make_rel(P, cfg, i):
    """एक दृश्य की खोज के सारे नियम (विषय, तारीख, पुरुष/महिला, ग़लत शब्द) एक जगह।"""
    ctx = P.get("ctx") or {}
    plans = P.get("plans") or []
    plan = plans[i] if i < len(plans) else None
    sc = P["scenes"][i]
    limits, days = REC_MODES.get(P.get("rec", "any"), ([None], None))
    year_from = (TODAY - datetime.timedelta(days=days)).year if days else None
    must = [m for m in (ctx.get("must_any") or []) if m]
    ex = set(ctx.get("off_topic") or []) | set(P.get("domain_ex") or [])
    if plan and isinstance(plan.get("exclude"), list):
        ex |= {str(e).strip().lower() for e in plan["exclude"]}
    own = set(must) | set(re.findall(r"[a-z]+", (P.get("anchor") or "").lower()))
    ex = {e for e in ex if len(e) >= 2 and e not in own}
    ex_re = (re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(e) for e in sorted(ex))
                        + r")(?![a-z0-9])") if ex else None)
    strict = limits[0] is not None
    sources = []
    if cfg.get("use_web") and not P.get("web_off"):
        sources.append("web")
    if cfg.get("pixabay_key") and not strict:
        sources.append("pixabay")
    sources += ["wikimedia", "openverse"]
    return {
        "P": P, "exclude": sorted(ex), "ex_re": ex_re, "must": must,
        "gender": ctx.get("gender", "none"), "year_from": year_from,
        "limits": limits, "strict": strict, "sources": sources,
        "used": P["used"], "hashes": P.setdefault("hashes", []),
        "key": cfg.get("gemini_key", ""), "models": P.get("models") or GEMINI_MODELS,
        "vision": bool(cfg.get("vision") and cfg.get("gemini_key") and not P.get("vision_off")),
        "topic": P.get("topic", ""), "scene": sc["text"],
        "rec_txt": REC_TEXT.get(P.get("rec", "any"), "any date"),
        "anchor": P.get("anchor", ""), "pixabay_key": cfg.get("pixabay_key", ""),
    }


def text_ok(text, year, R, words, need, strict_text=False):
    """शीर्षक/टैग देखकर फोटो का स्कोर। -1 = सीधे नकारो।"""
    t = (text or "").lower().replace("’", "'")
    if R["ex_re"] and R["ex_re"].search(t):
        return -1
    if not gender_ok(t, R["gender"]):
        return -1
    yf = R["year_from"]
    if yf:
        ys = years_in(t)
        if year:
            ys.append(year)
        if ys and max(ys) < yf:
            return -1  # पुरानी फोटो
        if not ys and R["strict"]:
            return -1  # ताज़ा ख़बर वाले विषय में तारीख अनजान फोटो नहीं
    sc = score_text(t, words) if words else 0
    must_hit = any(m in t for m in R["must"]) if R["must"] else True
    if strict_text or not R["vision"]:
        if not must_hit or (words and sc < need):
            return -1
    if R["gender"] == "women" and GENDER_W.search(t):
        sc += 1
    if must_hit:
        sc += 1
    return sc


def vision_failed(R, why):
    P = R["P"]
    P["vision_fail"] = P.get("vision_fail", 0) + 1
    if P["vision_fail"] >= 3:
        P["vision_off"] = True
        R["vision"] = False
        P["note"] = (P.get("note", "") + " Gemini से फोटो देखकर जाँचना बार-बार फेल हुआ "
                     f"({why[:90]}), इसलिए आगे सिर्फ टेक्स्ट से जाँच हुई।").strip()


def vision_pick(R, imgs):
    """Gemini को फोटो दिखाकर पूछता है कि कौन-सी दृश्य और कहानी से सही मेल खाती है।
    लौटाता है (क्रम, कारण); क्रम -1 = कोई ठीक नहीं; None = जाँच हो नहीं सकी।"""
    gender = {"men": "men's (male players/people only)",
              "women": "women's (female players/people only)"}.get(R["gender"], "any")
    bad = ", ".join(R["exclude"][:12]) or "none"
    prompt = (
        "You are a strict photo editor for a Hindi video.\n"
        f"Today's date: {TODAY.isoformat()}. Story: {R['topic']}\n"
        f"Photos must be: {R['rec_txt']}. People: {gender}.\n"
        f"Scene narration (Hindi): {R['scene'][:400]}\n"
        f"Never choose photos showing: {bad}.\n"
        f"Below are {len(imgs)} candidate photos. Choose the ONE that best illustrates this "
        "scene AND the story. REJECT a photo if: it shows a different sport, subject or person "
        "than the story (for example a wrestler in a cricket story); it is a logo, cartoon, "
        "meme, screenshot, map or poster full of text; it shows the wrong gender when a "
        "gender is specified; it looks old, black-and-white or low quality when recent photos "
        "are wanted; or it is only vaguely related. If none is clearly suitable answer -1.\n"
        'Reply ONLY JSON: {"best": <index or -1>, "reason": "<short English>"}'
    )
    parts = [{"text": prompt}]
    for i, im in enumerate(imgs):
        t = im.copy()
        t.thumbnail((512, 512))
        buf = io.BytesIO()
        t.save(buf, "JPEG", quality=80)
        parts.append({"text": f"Image {i}:"})
        parts.append({"inline_data": {
            "mime_type": "image/jpeg",
            "data": base64.b64encode(buf.getvalue()).decode(),
        }})
    text, err = gemini_generate(R["key"], "", R["models"], parts=parts)
    time.sleep(1)
    data = parse_json(text) if text else None
    if not data:
        return None, err or "जवाब समझ नहीं आया"
    try:
        b = int(data.get("best", -1))
    except Exception:
        return None, "जवाब समझ नहीं आया"
    if not (-1 <= b < len(imgs)):
        b = -1
    R["P"]["vision_fail"] = 0
    return b, str(data.get("reason", ""))[:140]


def try_download(cands, save_path, R, words, need, stats=None):
    scored, seen = [], set()
    for url, text, year in cands:
        if not url or url in R["used"] or url in seen:
            continue
        seen.add(url)
        s = text_ok(text, year, R, words, need)
        if s >= 0:
            scored.append((s, url, text, year))
    scored.sort(key=lambda x: -x[0])
    if stats is not None:
        stats["pass"] = len(scored)

    want = 4 if R["vision"] else 1
    got, tries = [], 0
    for _, url, text, year in scored:
        if tries >= 10 or len(got) >= want:
            break
        tries += 1
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            if len(r.content) > 15_000_000:
                continue
            with Image.open(io.BytesIO(r.content)) as img:
                rgb = to_rgb(img)
            if rgb.width < 500 or rgb.height < 350 or is_blank(rgb):
                continue
            h = ahash(rgb)
            if is_dup(h, R["hashes"]):
                continue  # वही या बहुत मिलती-जुलती फोटो पहले लग चुकी है
            got.append((url, rgb, h, text, year))
        except Exception:
            continue
    if not got:
        return False

    pick = 0
    if R["vision"]:
        idx, why = vision_pick(R, [g[1] for g in got])
        if stats is not None:
            stats["vision"] = why
        if idx is None:
            vision_failed(R, why)
            ok = [k for k, g in enumerate(got)
                  if text_ok(g[3], g[4], R, words, need, strict_text=True) >= 0]
            if not ok:
                return False
            pick = ok[0]
        elif idx < 0:
            return False  # AI ने देखकर सब नकार दीं
        else:
            pick = idx

    url, rgb, h, _, _ = got[pick]
    rgb.thumbnail((2000, 2000))
    rgb.save(save_path, "JPEG", quality=90)
    R["used"].add(url)
    R["hashes"].append(h)
    return True


SRC_LABEL = {"web": "इंटरनेट", "pixabay": "Pixabay", "wikimedia": "Wikimedia", "openverse": "Openverse"}


def fetch_source(name, q, R, lim, errs):
    if name == "web":
        qq = q
        if R["limits"][0] is not None and not re.search(r"\b20\d\d\b", q):
            qq = f"{q} {TODAY.year}"
        res = web_urls(qq, errs, lim)
        yr = TODAY.year if lim in ("d", "w", "m") else (TODAY.year - 1 if lim == "y" else None)
        return [(u, t, yr) for u, t, _ in res]
    if name == "pixabay":
        return pixabay_urls(q, R["pixabay_key"], errs)
    if name == "wikimedia":
        return wikimedia_urls(q, errs)
    return openverse_urls(q, errs)


def find_photo(queries, save_path, R, log=None):
    P = R["P"]
    for q in queries:
        words = match_words(q, R["anchor"])
        need = 2 if len(words) >= 3 else 1
        for name in R["sources"]:
            if name == "web" and P.get("web_off"):
                continue
            for lim in (R["limits"] if name == "web" else [None]):
                errs, stats = [], {}
                cands = fetch_source(name, q, R, lim, errs)
                if name == "web":
                    time.sleep(0.6)
                    if errs and not cands:
                        P["web_fail"] = P.get("web_fail", 0) + 1
                        if P["web_fail"] >= 4:
                            P["web_off"] = True
                            P["note"] = (P.get("note", "") + " इंटरनेट खोज बार-बार ब्लॉक हुई, "
                                         "इसलिए आगे उसे छोड़ दिया।").strip()
                    elif cands:
                        P["web_fail"] = 0
                got = try_download(cands, save_path, R, words, need, stats)
                if log is not None:
                    line = (f"'{q}' → {SRC_LABEL[name]}"
                            + (f" ({lim})" if lim else "")
                            + f": {len(cands)} मिलीं, {stats.get('pass', 0)} नियमों पर खरी"
                            + (f", AI जाँच: {stats['vision']}" if stats.get("vision") else "")
                            + (" ✅ ली गई" if got else ""))
                    if errs:
                        line += f" | समस्या: {errs[0]}"
                    log.append(line)
                if got:
                    return SRC_LABEL[name], q
    return None, None


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
    for rnd in range(2):
        for model in ("flux", None):
            if time.time() - started > 170:
                return False
            try:
                params = {
                    "width": w, "height": h, "nologo": "true",
                    "seed": random.randint(1, 1000000),
                }
                if model:
                    params["model"] = model
                r = requests.get(
                    "https://image.pollinations.ai/prompt/" + quote(full[:600]),
                    params=params, headers=HEADERS, timeout=75,
                )
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
                    time.sleep(8)
                else:
                    _note(err, f"AI सेवा ने {r.status_code} दिया")
            except Exception as e:
                _note(err, f"AI: {type(e).__name__}: {str(e)[:70]}")
        time.sleep(4)
    return False


def ai_scene(R, prompt, path, size, log):
    """सही विषय वाली AI तस्वीर: बनवाकर Gemini से जँचवाता है, ग़लत हो तो दोबारा बनवाता है।"""
    errs = []
    for _ in range(2):
        if not prompt or not ai_image(prompt, path, size, errs):
            continue
        try:
            with Image.open(path) as im:
                rgb = to_rgb(im)
        except Exception:
            continue
        h = ahash(rgb)
        if is_dup(h, R["hashes"]):
            errs.append("AI तस्वीर पहले जैसी निकली")
            continue
        if R["vision"]:
            idx, why = vision_pick(R, [rgb])
            if idx is None:
                vision_failed(R, why)
            elif idx < 0:
                errs.append("AI तस्वीर विषय से मेल नहीं खाई: " + why)
                continue
        R["hashes"].append(h)
        return True
    if errs:
        log.append("AI: " + "; ".join(sorted(set(errs))))
    return False


def make_text_card(path, size, idx):
    """तस्वीर न मिलने पर आधुनिक रंगीन पृष्ठभूमि। शब्द वीडियो बनाते समय ऊपर लिखे जाते हैं।"""
    w, h = size
    c1, c2, c3 = CARD_BG[idx % len(CARD_BG)]
    base = ImageOps.colorize(Image.linear_gradient("L").resize((w, h)), c1, c2)
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(glow)
    d.ellipse([w * 0.45, -h * 0.12, w * 1.35, h * 0.45], fill=c3)
    d.ellipse([-w * 0.4, h * 0.6, w * 0.6, h * 1.2], fill=c3)
    glow = glow.filter(ImageFilter.GaussianBlur(max(20, min(w, h) // 5)))
    out = ImageChops.screen(base, glow)
    bar = CARD_ACCENT[idx % len(CARD_ACCENT)][0]
    ImageDraw.Draw(out).rectangle(
        [int(w * 0.07), int(h * 0.16), int(w * 0.07) + int(w * 0.16),
         int(h * 0.16) + max(6, h // 140)],
        fill=bar,
    )
    out.save(path, "JPEG", quality=92)


def scene_queries_for(P, i):
    plans = P.get("plans") or []
    plan = plans[i] if i < len(plans) else None
    anchor = P.get("anchor", "")
    qs = []
    if plan:
        raw = plan.get("queries") or ([plan["query"]] if plan.get("query") else [])
        qs = [with_anchor(str(q).strip(), anchor) for q in raw if str(q).strip()][:2]
    if not qs:
        fq, _ = scene_queries(P["scenes"][i]["text"], P.get("topic", ""), anchor)
        qs = fq[:2]
    if anchor and anchor not in qs:
        qs.append(anchor)
    seen, final = set(), []
    for q in qs:
        if q.lower() not in seen:
            seen.add(q.lower())
            final.append(q)
    return final


def ai_prompt_for(P, i):
    plans = P.get("plans") or []
    plan = plans[i] if i < len(plans) else None
    sc = P["scenes"][i]
    if plan and plan.get("ai"):
        base = str(plan["ai"])
    else:
        base = visual_prompt(sc["text"], P.get("topic", ""), P.get("anchor", ""))
    return with_anchor(base, P.get("anchor", "")) if base else ""


def fill_scene(P, cfg, i, order=("real", "ai", "card")):
    """एक दृश्य की तस्वीर तय करता है: असली फोटो → AI फोटो → टेक्स्ट कार्ड।
    कभी रंग की सादी पृष्ठभूमि या पुरानी तस्वीर दोहराना नहीं। True = तस्वीर लगी।"""
    sc = P["scenes"][i]
    size = tuple(P["size"])
    sc["ver"] = sc.get("ver", 0) + 1
    path = os.path.join(P["folder"], f"scene_{i}_{sc['ver']}.jpg")
    R = make_rel(P, cfg, i)
    log = []

    if "real" in order:
        source, query = find_photo(scene_queries_for(P, i), path, R, log)
        if source:
            sc.update(image=path, source=source, query=query, kind="photo", log=log)
            return True

    if "ai" in order and cfg.get("use_ai"):
        prompt = ai_prompt_for(P, i)
        if not prompt:
            log.append("AI: दृश्य का तस्वीर-विवरण नहीं बन सका")
        elif ai_scene(R, prompt, path, size, log):
            sc.update(image=path, source="AI", query=prompt[:120], kind="ai", log=log)
            return True

    if "card" in order:
        make_text_card(path, size, i)
        sc.update(image=path, source="टेक्स्ट कार्ड", query="", kind="card", log=log)
        P.setdefault("missing", []).append(i + 1)
        return True

    sc["log"] = log
    return False


def diagnose_services(pixabay_key="", gemini_key=""):
    """हर बाहरी सेवा को एक छोटे परीक्षण से जाँचता है: [(नाम, ठीक?, विवरण)]"""
    rows = []

    en = translate_hi_en("नमस्ते दोस्तो")
    if GoogleTranslator is None:
        rows.append(("Google अनुवाद", False, "deep-translator इंस्टॉल नहीं है"))
    else:
        rows.append(("Google अनुवाद", bool(en.strip()), en[:40] or "जवाब नहीं आया"))

    if gemini_key:
        ok, msg = test_gemini(gemini_key)
        rows.append(("Gemini (key और फोटो जाँचने वाला AI)", ok, msg))

    errs = []
    res = web_urls("India cricket match", errs, "w")
    rows.append(("इंटरनेट खोज (पिछले हफ़्ते की फोटो)", len(res) > 0,
                 f"{len(res)} मिलीं" + (f" | {errs[0]}" if errs else "")))
    for name, fn in (("Wikimedia", wikimedia_urls), ("Openverse", openverse_urls)):
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


# ---------------------------------------------------------------- कैप्शन और टेक्स्ट कार्ड
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


def card_markup(text, w, h, accent):
    """टेक्स्ट कार्ड के शब्द: बड़े अक्षर, कई पंक्तियाँ, सबसे लंबा शब्द रंगीन।"""
    words = (text or "").replace("\\", "").replace("{", "(").replace("}", ")").upper().split()
    per = 10 if h > w else (13 if h == w else 18)
    lines, cur = [], ""
    for wd in words:
        if cur and len(cur) + 1 + len(wd) > per:
            lines.append(cur)
            cur = wd
        else:
            cur = (cur + " " + wd).strip()
    if cur:
        lines.append(cur)
    lines = lines[:4]
    hl = max(words, key=len) if words else ""
    done, out = False, []
    for ln in lines:
        parts = []
        for wd in ln.split():
            if not done and wd == hl:
                parts.append("{\\c" + accent + "}" + wd + "{\\c&H00FFFFFF&}")
                done = True
            else:
                parts.append(wd)
        out.append(" ".join(parts))
    return "\\N".join(out)


def write_ass(path, cues, size, font_path, size_ratio, lead,
              card=None, card_idx=0, total=0.0):
    w, h = size
    family, is_bold = font_info(font_path)
    if not family:
        return False
    bold = 0 if is_bold else -1

    fontsize = int(min(w, h) * size_ratio * (1.0 if w <= h else 0.95))
    outline = max(2, fontsize // 14)
    margin_v = int(h * (0.14 if h > w else 0.09))
    margin_lr = int(w * 0.07)
    cs = int(min(w, h) * (0.15 if h > w else 0.16))
    cout = max(3, cs // 16)

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
        f"&H00000000,&H80000000,{bold},0,0,0,100,100,0,0,1,"
        f"{outline},2,2,{margin_lr},{margin_lr},{margin_v},1",
        f"Style: Card,{family},{cs},&H00FFFFFF,&H000000FF,"
        f"&H00000000,&H96000000,{bold},0,0,0,100,100,2,0,1,"
        f"{cout},4,5,{margin_lr},{margin_lr},0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text",
    ]

    if card:
        accent = CARD_ACCENT[card_idx % len(CARD_ACCENT)][1]
        body = card_markup(card, w, h, accent)
        ms = int(max(1.0, total) * 1000)
        anim = (r"{\fad(250,200)\fscx88\fscy88\t(0,400,\fscx100\fscy100)"
                r"\t(400," + str(ms) + r",\fscx106\fscy106)}")
        lines.append(f"Dialogue: 0,{ass_time(0.0)},{ass_time(total + 0.5)},Card,,0,0,0,,{anim}{body}")

    for start, end, text in cues or []:
        clean = text.replace("\\", "").replace("{", "(").replace("}", ")")
        pop = r"{\fad(100,60)\fscx88\fscy88\t(0,140,\fscx100\fscy100)}"
        lines.append(
            f"Dialogue: 1,{ass_time(start + lead)},{ass_time(end + lead)},"
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
              pad, is_last, card_text=None, card_idx=0):
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

    if font and (cues or card_text):
        ass_path = os.path.join(
            folder, f"cap_{hashlib.md5(out.encode()).hexdigest()[:8]}.ass"
        )
        if write_ass(ass_path, cues, size, font, size_ratio, LEAD,
                     card_text, card_idx, total):
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
            data["hashes"] = list(data.get("hashes", []))
            data["size"] = tuple(data.get("size") or (720, 1280))
            for sc in data.get("scenes", []):
                sc["cues"] = [tuple(c) for c in sc.get("cues", [])]
            if data.get("state") in ("drafting", "rendering"):
                if data.get("draft_done"):
                    data["state"] = "ready"
                    data["note"] = (
                        "पिछली बार वीडियो बनते-बनते ऐप रुक गया था। "
                        "'वहीं से आगे बढ़ाएँ' दबाएँ। जो क्लिप बन चुके हैं वे दोबारा नहीं बनेंगे।"
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
    """आवाज़, कहानी की समझ, फोटो-योजना और तस्वीरें तैयार करता है। रुकने पर वहीं से आगे बढ़ता है।"""
    try:
        folder = P["folder"]
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        resume = bool(cfg.get("resume")) and bool(P.get("scenes"))
        key = cfg["gemini_key"]

        P["state"] = "drafting"
        P["error"] = ""
        if resume:
            for k in ("vision_fail", "vision_off", "web_fail", "web_off"):
                P.pop(k, None)

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
                    "headline": auto_headline(text),
                    "voice_path": vpath,
                    "voice_len": vlen,
                    "cues": build_cues(text, bounds, vlen),
                    "pad": round(random.uniform(0.3, 0.55), 2)
                    if cfg["natural"] else 0.35,
                    "image": None,
                    "source": "",
                    "query": "",
                    "kind": "",
                    "ver": 0,
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

        # 2) कहानी की समझ और हर दृश्य की योजना (बनी हुई हो तो वही)
        if not P.get("planned"):
            set_state(P, "पूरी कहानी पढ़कर विषय, तारीख और पुरुष/महिला तय हो रहे हैं...", 26)
            extra = cfg["extra_topic"].strip()
            models = []
            if key:
                models, _ = gemini_models(key)
            models = models or GEMINI_MODELS
            P["models"] = models

            data, gerr = (analyze_story(P["script"], extra, key, models)
                          if key else (None, ""))
            ctx = normalize_ctx(data) if data else {}
            ok = bool(data)

            topic = (ctx.get("topic_en") or extra
                     or translate_hi_en(P["script"], 600) or P["script"][:200])
            anchor = extra or ctx.get("anchor") or ""
            if not anchor:
                kws = english_keywords(topic)
                anchor = kws[0] if kws else ""
            blob = f"{topic} {anchor} {extra} {P['script'][:1500]}"

            modern = (ctx["era"] == "modern") if ok else guess_modern(topic, P["script"], extra)
            gender = cfg["gender"] if cfg["gender"] != "auto" else (
                ctx.get("gender", "none") if ok else guess_gender(blob))
            rec = cfg["recency"] if cfg["recency"] != "auto" else (
                ctx.get("recency") or ("year" if modern else "any"))
            if not modern and cfg["recency"] == "auto":
                rec = "any"

            must = list(ctx.get("must_any") or [])
            for wd in re.findall(r"[a-z]{3,}", f"{anchor} {extra}".lower()):
                if wd not in must and wd not in STOPWORDS:
                    must.append(wd)
            ctx.update(gender=gender, must_any=must[:10], era="modern" if modern else "historical")
            ctx.setdefault("off_topic", [])
            P.update(ctx=ctx, anchor=anchor, topic=topic, rec=rec, modern=modern,
                     domain_ex=domain_excludes(blob))
            if key and not ok:
                P["note"] = ("Gemini से कहानी की समझ नहीं बन सकी, सामान्य तरीका इस्तेमाल हुआ। "
                             f"कारण: {gerr}")

            set_state(P, "हर दृश्य के लिए फोटो-योजना बन रही है...", 28)
            plans = [None] * n
            if key:
                plans, perr = plan_with_ai(texts, P, key, models)
                if not any(plans) and not P.get("note"):
                    P["note"] = ("Gemini से दृश्यों की योजना नहीं बन सकी, सामान्य तरीका "
                                 f"इस्तेमाल हुआ। कारण: {perr}")
            P["plans"] = plans
            for sc, pl in zip(scenes, plans):
                hl = str((pl or {}).get("headline") or "").strip()
                if hl:
                    sc["headline"] = hl
            P["planned"] = True
            save_project(P)

        # 3) तस्वीरें (जिनकी बन चुकी हैं उन्हें छोड़ देता है)
        uploads = cfg["uploads"]
        for i, sc in enumerate(scenes):
            if sc.get("image") and os.path.exists(sc["image"]):
                continue

            set_state(P, f"सही तस्वीर खोज रहे हैं: दृश्य {i + 1}/{n}",
                      27 + (i + 1) / n * 70)

            if i < len(uploads):
                path = os.path.join(folder, f"scene_{i}_u.jpg")
                shutil.copy(uploads[i], path)
                sc.update(image=path, source="आपकी तस्वीर", query="", kind="photo")
                save_project(P)
                continue

            fill_scene(P, cfg, i)
            save_project(P)

        cards = sorted(set(P.get("missing", [])))
        if cards:
            P["note"] = (P.get("note", "") +
                         f" इन दृश्यों की मेल खाती फोटो नहीं मिली: {cards}। "
                         "इनमें बड़े अक्षरों वाला टेक्स्ट कार्ड लगा है। "
                         "एडिट में 'नई फोटो खोजो' या अपनी फोटो लगा सकते हैं।").strip()

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
        size = tuple(cfg["size"])
        P["error"] = ""
        kept = [dict(s, i=n) for n, s in enumerate(P["scenes"]) if s["keep"]]
        n = len(kept)
        if n == 0:
            raise RuntimeError("कम से कम एक दृश्य रखना ज़रूरी है।")

        # कोई दृश्य बिना तस्वीर का न रहे: सादा रंग नहीं, टेक्स्ट कार्ड लगेगा
        for sc in kept:
            if not (sc.get("image") and os.path.exists(sc["image"])):
                path = os.path.join(folder, f"scene_{sc['i']}_r.jpg")
                make_text_card(path, size, sc["i"])
                sc.update(image=path, kind="card", source="टेक्स्ट कार्ड")
                P["scenes"][sc["i"]].update(image=path, kind="card", source="टेक्स्ट कार्ड")

        font = None
        need_font = cfg["captions"] or any(s.get("kind") == "card" for s in kept)
        if need_font:
            font = get_caption_font()
            if not font:
                P["note"] = "हिंदी फ़ॉन्ट नहीं मिला, इसलिए कैप्शन/टेक्स्ट कार्ड के शब्द नहीं दिखेंगे।"

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
            card_text = (sc.get("headline") or auto_headline(sc["text"])) \
                if sc.get("kind") == "card" else None
            cues = sc["cues"] if (font and cfg["captions"]) else None

            sig = repr((
                sc["image"], os.path.getmtime(sc["image"]),
                prev, prev_eff, effects[idx], trans[idx] if prev else "",
                sc["voice_path"], os.path.getmtime(sc["voice_path"]),
                cues, card_text, sc["i"] % len(CARD_ACCENT), font, size,
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
                    sc["voice_len"], cues, tmp,
                    size, effects[idx], trans[idx], ffmpeg, font,
                    cfg["cap_ratio"], folder, sc["pad"], idx == n - 1,
                    card_text=card_text, card_idx=sc["i"],
                )
                os.replace(tmp, clip)
            clips.append(clip)

        set_state(P, "पूरा वीडियो जोड़ा जा रहा है...", 92)
        stamp = int(time.time())
        joined = os.path.join(folder, f"video_{stamp}.mp4")
        join_clips(clips, folder, joined, ffmpeg)

        final = joined
        if cfg.get("music") and os.path.exists(cfg["music"]):
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


def edit_job(P, cfg):
    """एडिट के बाद: बदले हुए टेक्स्ट की आवाज़ दोबारा बनाकर सिर्फ बदले क्लिप फिर से बनाता है।"""
    try:
        P["cancel"] = False
        P["error"] = ""
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        dirty = [i for i, sc in enumerate(P["scenes"]) if sc.get("text_dirty")]
        for n, i in enumerate(dirty):
            sc = P["scenes"][i]
            set_state(P, f"आवाज़ दोबारा बन रही है: दृश्य {i + 1}", 2 + n / max(1, len(dirty)) * 20)
            if sc.get("own_voice"):
                sc["cues"] = build_cues(sc["text"], [], sc["voice_len"])
            else:
                rate, pitch = voice_params(sc["text"], P.get("speed", -5), P.get("natural", True))
                vpath = os.path.join(P["folder"], f"voice_{i}_{int(time.time())}.mp3")
                bounds = asyncio.run(synth(sc["text"], P["voice"], vpath, rate, pitch))
                vlen = audio_duration(vpath, ffmpeg)
                sc.update(voice_path=vpath, voice_len=vlen,
                          cues=build_cues(sc["text"], bounds, vlen))
            sc["text_dirty"] = False
        save_project(P)
        P["state"] = "rendering"
        render_job(P, cfg["render"])
    except Exception as e:
        P["state"] = "error"
        P["error"] = str(e)


def start_thread(fn, P, cfg):
    t = threading.Thread(target=fn, args=(P, cfg), daemon=True)
    t.start()


# ==== UI ====
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


SETTING_KEYS = [
    "voice_name", "speed", "natural", "format_name", "quality", "effect_choice",
    "trans_choice", "cap_name", "max_chars", "captions_on", "use_web", "use_ai",
    "use_vision", "gem_key", "extra_topic", "pix_key", "gender_choice", "recency_choice",
]

st.title("🎬 Ravi AI Auto Video Creator")
st.caption(APP_VERSION)

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


def go_new():
    st.session_state["fresh"] = True
    st.query_params.clear()
    st.rerun()


# ---------------------------------------------------------------- चल रहा काम
if busy:
    # काम के दौरान सेटिंग के खाने दिखते नहीं; उनकी चुनी हुई कीमतें खोने न दें
    for _k in SETTING_KEYS:
        if _k in st.session_state:
            st.session_state[_k] = st.session_state[_k]
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

# पन्ने के हिस्से: ऊपर नतीजा/एडिट, फिर नई कहानी, फिर सेटिंग
c_results = st.container()
c_story = st.container()
c_settings = st.expander("⚙️ सेटिंग (आवाज़, आकार, फोटो के नियम, key)", expanded=(P is None))

# ---------------------------------------------------------------- सेटिंग
with c_settings:
    voice_name = st.radio("🎙️ आवाज़", list(VOICES.keys()), horizontal=True, key="voice_name")
    speed = st.slider("बोलने की गति", -25, 15, -5, 1, key="speed",
                      help="ऋण का मतलब थोड़ा धीरे")
    natural = st.checkbox(
        "🗣️ प्राकृतिक आवाज़ (हर दृश्य में थोड़ी अलग गति/सुर, प्रश्न और विस्मय पर बदलाव)",
        value=True, key="natural",
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

    s1, s2 = st.columns(2)
    with s1:
        format_name = st.selectbox("📐 वीडियो का आकार", list(FORMATS.keys()), key="format_name")
        quality = st.selectbox(
            "🎞️ वीडियो क्वालिटी",
            ["HD 1080p (बेहतर, थोड़ा धीमा)", "सामान्य 720p (तेज़)"],
            key="quality",
        )
        gender_label = st.selectbox("👥 फोटो में कौन?", list(GENDER_LABELS.keys()),
                                    key="gender_choice",
                                    help="महिला चुनने पर सिर्फ महिलाओं की फोटो, पुरुष पर सिर्फ पुरुषों की")
        recency_label = st.selectbox("📅 फोटो कितनी नई?", list(REC_LABELS.keys()),
                                     key="recency_choice",
                                     help="जैसे आज के मैच की कहानी में आज/इस हफ़्ते की फोटो")
    with s2:
        effect_choice = st.selectbox(
            "✨ फोटो इफेक्ट", ["Auto (हर दृश्य में अलग)"] + EFFECTS, key="effect_choice")
        trans_choice = st.selectbox(
            "🔀 ट्रांज़िशन", ["Auto (हर दृश्य अलग)"] + TRANSITIONS, key="trans_choice")
        cap_name = st.select_slider(
            "कैप्शन का आकार", list(CAPTION_SIZES.keys()), value="मध्यम", key="cap_name")

    max_chars = st.slider(
        "✂️ एक दृश्य में अधिकतम अक्षर (कम = तस्वीरें जल्दी-जल्दी बदलेंगी)",
        60, 400, 110, 10, key="max_chars",
    )
    captions_on = st.checkbox("💬 हिंदी कैप्शन (बोले जा रहे शब्द स्क्रीन पर)",
                              value=True, key="captions_on")
    use_web = st.checkbox(
        "🌐 इंटरनेट की तस्वीरों में खोजें (कॉपीराइट आपकी ज़िम्मेदारी है)",
        value=True, key="use_web")
    use_ai = st.checkbox("🤖 असली फोटो न मिले तो सही विषय की AI फोटो बनाएँ",
                         value=True, key="use_ai")
    use_vision = st.checkbox(
        "🧠 AI हर फोटो को देखकर जाँचे (धीमा, पर ग़लत फोटो नहीं लगेगी)",
        value=True, key="use_vision",
        help="Gemini key चाहिए। हर फोटो को देखकर तय होता है कि वह कहानी, खिलाड़ी/विषय और तारीख से मेल खाती है या नहीं।")

    gemini_key = st.text_input(
        "Google Gemini API key (मुफ़्त; aistudio.google.com से; सबसे सटीक तस्वीरों के लिए)",
        type="password", key="gem_key",
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
            st.warning("पहले key चिपकाएँ।")
        else:
            ok, msg = test_gemini(gemini_key.strip())
            (st.success if ok else st.error)(msg)

    extra_topic = st.text_input(
        "कहानी का मुख्य विषय English में (वैकल्पिक, हर खोज में जुड़ेगा)",
        placeholder="India women vs Australia women ODI", key="extra_topic",
    )
    pixabay_key = st.text_input("Pixabay API key (वैकल्पिक, मुफ़्त)", type="password",
                                key="pix_key")
    if not pixabay_key.strip() and secret("PIXABAY_API_KEY"):
        pixabay_key = secret("PIXABAY_API_KEY")
        st.caption("✅ Pixabay key पहले से सेट है (Secrets से)।")

    if st.button("🧪 सेवाएँ जाँचें (तस्वीर/AI क्यों नहीं मिल रही)"):
        with st.spinner("हर सेवा जाँची जा रही है... (1-2 मिनट लग सकते हैं)"):
            rows = diagnose_services(pixabay_key.strip(), gemini_key.strip())
        for name, good, detail in rows:
            (st.success if good else st.error)(f"{name}: {detail}")

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
        "use_web": use_web, "use_ai": use_ai, "vision": use_vision,
        "gemini_key": gemini_key.strip(), "pixabay_key": pixabay_key.strip(),
        "extra_topic": extra_topic, "uploads": uploads or [],
        "gender": GENDER_LABELS[gender_label], "recency": REC_LABELS[recency_label],
        "render": {
            "size": render_size, "default_effect": effect_choice,
            "default_transition": trans_choice,
            "captions": captions_on, "cap_ratio": CAPTION_SIZES[cap_name],
            "music": None, "music_vol": 0.1,
        },
    }


KIND_LABEL = {"photo": "असली फोटो", "ai": "AI फोटो", "card": "टेक्स्ट कार्ड"}


def editor(P, cfg):
    """हर दृश्य का एडिटर: टेक्स्ट, फोटो, इफेक्ट, ट्रांज़िशन, अपनी फोटो/आवाज़, संगीत।"""
    pid = P["id"]
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    st.subheader("✏️ एडिट करें")
    st.caption(
        "बदलाव करके सबसे नीचे 'दोबारा जोड़ें' दबाएँ। सिर्फ बदले हुए क्लिप दोबारा बनेंगे, "
        "वीडियो सर्वर पर ही रहेगा।"
    )

    mus = st.file_uploader("🎵 पृष्ठभूमि संगीत (वैकल्पिक)",
                           type=["mp3", "wav", "m4a", "aac", "ogg"], key=f"mus_{pid}")
    P["music_vol"] = st.slider("संगीत की आवाज़", 0.02, 0.40,
                               float(P.get("music_vol", 0.10)), 0.01, key=f"vol_{pid}")
    if mus is not None:
        sig = f"{mus.name}-{mus.size}"
        if P.get("music_sig") != sig:
            mp = os.path.join(P["folder"], "music" + (os.path.splitext(mus.name)[1] or ".mp3"))
            with open(mp, "wb") as f:
                f.write(mus.getvalue())
            P["music_path"], P["music_sig"] = mp, sig
    if P.get("music_path"):
        st.caption("🎵 संगीत लगा हुआ है।")
        if st.checkbox("संगीत हटाएँ", key=f"nomus_{pid}"):
            P["music_path"], P["music_sig"] = None, None

    for i, sc in enumerate(P["scenes"]):
        label = KIND_LABEL.get(sc.get("kind"), "")
        with st.expander(f"दृश्य {i + 1} · {label} · {sc['text'][:30]}…"):
            if sc.get("image") and os.path.exists(sc["image"]):
                st.image(sc["image"], width=240, caption=f"{sc.get('source', '')} {sc.get('query', '')[:60]}")

            new_text = st.text_area("बोला जाने वाला टेक्स्ट", sc["text"], height=110,
                                    key=f"tx_{pid}_{i}")
            if new_text.strip() and new_text.strip() != sc["text"]:
                sc["text"], sc["text_dirty"] = new_text.strip(), True
            sc["headline"] = st.text_input(
                "बड़े अक्षरों में दिखने वाला टेक्स्ट (जब फोटो की जगह टेक्स्ट कार्ड हो)",
                sc.get("headline", ""), key=f"hl_{pid}_{i}")

            e1, e2 = st.columns(2)
            with e1:
                opts = ["Default"] + EFFECTS
                sc["effect"] = st.selectbox("इफेक्ट", opts, key=f"ef_{pid}_{i}",
                                            index=opts.index(sc["effect"]) if sc["effect"] in opts else 0)
            with e2:
                topts = ["Default"] + TRANSITIONS
                sc["transition"] = st.selectbox("ट्रांज़िशन", topts, key=f"tr_{pid}_{i}",
                                                index=topts.index(sc["transition"]) if sc["transition"] in topts else 0)
            sc["keep"] = st.checkbox("यह दृश्य रखें", sc.get("keep", True), key=f"kp_{pid}_{i}")

            b1, b2, b3 = st.columns(3)
            if b1.button("🔄 नई फोटो", key=f"ns_{pid}_{i}", use_container_width=True):
                with st.spinner("सही फोटो खोज रहे हैं..."):
                    ok = fill_scene(P, cfg, i, order=("real",))
                if ok:
                    save_project(P)
                    st.rerun()
                else:
                    st.warning("नियमों पर खरी दूसरी फोटो नहीं मिली। नीचे 'खोज की जाँच' देखें।")
            if b2.button("🤖 AI फोटो", key=f"ai_{pid}_{i}", use_container_width=True):
                with st.spinner("AI फोटो बन रही है (1 मिनट तक)..."):
                    ok = fill_scene(P, dict(cfg, use_ai=True), i, order=("ai",))
                if ok:
                    save_project(P)
                    st.rerun()
                else:
                    st.warning("सही विषय की AI फोटो नहीं बन सकी। टेक्स्ट कार्ड या अपनी फोटो लगाएँ।")
            if b3.button("🔤 टेक्स्ट कार्ड", key=f"cd_{pid}_{i}", use_container_width=True):
                fill_scene(P, cfg, i, order=("card",))
                save_project(P)
                st.rerun()

            up = st.file_uploader("या अपनी फोटो लगाएँ", key=f"up_{pid}_{i}")
            if up is not None and sc.get("upl_sig") != f"{up.name}-{up.size}":
                try:
                    with Image.open(up) as im:
                        rgb = to_rgb(im)
                    rgb.thumbnail((2400, 2400))
                    sc["ver"] = sc.get("ver", 0) + 1
                    path = os.path.join(P["folder"], f"scene_{i}_{sc['ver']}.jpg")
                    rgb.save(path, "JPEG", quality=92)
                    sc.update(image=path, source="आपकी तस्वीर", query="", kind="photo",
                              upl_sig=f"{up.name}-{up.size}")
                    save_project(P)
                    st.rerun()
                except Exception:
                    st.error("यह फ़ाइल तस्वीर के रूप में नहीं खुली।")

            av = st.file_uploader("या अपनी आवाज़ की रिकॉर्डिंग लगाएँ (इसी दृश्य के लिए)",
                                  type=["mp3", "wav", "m4a", "aac", "ogg", "opus"],
                                  key=f"av_{pid}_{i}")
            if av is not None and sc.get("voice_sig") != f"{av.name}-{av.size}":
                try:
                    src = os.path.join(P["folder"], f"own_{i}_{av.size}" + (os.path.splitext(av.name)[1] or ".mp3"))
                    with open(src, "wb") as f:
                        f.write(av.getvalue())
                    dst = os.path.join(P["folder"], f"voice_{i}_own_{av.size}.mp3")
                    r = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                                        "-i", src, "-ar", "44100", "-ac", "2", dst],
                                       capture_output=True, text=True)
                    if r.returncode != 0:
                        raise RuntimeError(r.stderr[-300:])
                    vlen = audio_duration(dst, ffmpeg)
                    sc.update(voice_path=dst, voice_len=vlen, own_voice=True,
                              cues=build_cues(sc["text"], [], vlen),
                              voice_sig=f"{av.name}-{av.size}")
                    save_project(P)
                    st.success("आपकी आवाज़ लग गई। नीचे 'दोबारा जोड़ें' दबाएँ।")
                except Exception as e:
                    st.error(f"आवाज़ की फ़ाइल नहीं चली: {e}")

            if sc.get("log"):
                st.caption("खोज की जाँच:")
                st.code("\n".join(sc["log"]), language=None)

    if st.button("🔁 बदलाव लागू करके वीडियो दोबारा जोड़ें", type="primary",
                 use_container_width=True, key=f"apply_{pid}"):
        rc = dict(cfg["render"])
        rc["size"] = tuple(P["size"])
        rc["music"] = P.get("music_path")
        rc["music_vol"] = P.get("music_vol", 0.10)
        P["state"], P["progress"], P["status"] = "rendering", 0, "बदलाव लागू हो रहे हैं..."
        P["heartbeat"] = time.time()
        save_project(P)
        start_thread(edit_job, P, {"render": rc})
        st.rerun()


# ---------------------------------------------------------------- तैयार वीडियो / अधूरा काम
resume_clicked = False
with c_results:
    if P is not None:
        video_ready = bool(P.get("video")) and os.path.exists(P["video"])

        if P.get("note"):
            st.warning(P["note"])
        if P["state"] == "error" and P.get("error"):
            st.error("बीच में समस्या आई।")
            st.code(P["error"][-600:])

        if video_ready:
            st.success("वीडियो तैयार है!")
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
        else:
            done_n = sum(1 for sc in P["scenes"] if sc.get("image"))
            if P["scenes"]:
                st.warning(
                    f"वीडियो अभी पूरा नहीं बना (तस्वीरें {done_n}/{len(P['scenes'])})। "
                    "जो बन चुका है वह दोबारा नहीं बनेगा।"
                )
            resume_clicked = st.button("▶️ वहीं से आगे बढ़ाएँ", type="primary")

        if P.get("draft_done") and P["scenes"]:
            kinds = [sc.get("kind") for sc in P["scenes"]]
            st.caption(
                f"दृश्य: {kinds.count('photo')} असली फोटो · {kinds.count('ai')} AI फोटो · "
                f"{kinds.count('card')} टेक्स्ट कार्ड"
            )
            editor(P, make_cfg())

        st.divider()
        if st.button("🆕 नया वीडियो बनाएँ"):
            go_new()

# ---------------------------------------------------------------- नई कहानी
with c_story:
    st.subheader("📝 नई कहानी")
    script = st.text_area("पूरी हिंदी कहानी लिखें", height=260)
    uploaded_images = st.file_uploader(
        "🖼️ अपनी तस्वीरें (पहले दृश्यों में क्रम से लगेंगी)",
        accept_multiple_files=True,
    )
    if uploaded_images:
        st.caption(f"✅ {len(uploaded_images)} फ़ाइल मिली")
    start_clicked = st.button("🎬 वीडियो बनाना शुरू करें", type="primary", disabled=busy)

if resume_clicked and P is not None:
    P["error"], P["note"], P["cancel"] = "", "", False
    P["heartbeat"] = time.time()
    P["state"] = "drafting" if not P.get("draft_done") else "rendering"
    start_thread(full_job, P, make_cfg(resume=True))
    st.rerun()

if start_clicked:
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
        "note": "", "error": "", "scenes": [], "used": set(), "hashes": [],
        "video": None, "anchor": "", "topic": "", "modern": False, "offset": 0,
        "ctx": {}, "plans": [], "rec": "any", "domain_ex": [], "models": [],
        "voice": VOICES[voice_name], "speed": speed, "natural": natural,
        "size": render_size, "cancel": False, "static_name": "",
    }
    JOBS[new_id] = P
    st.session_state["fresh"] = False
    start_thread(full_job, P, make_cfg(uploads=uploads))
    st.query_params["p"] = new_id
    st.rerun()
