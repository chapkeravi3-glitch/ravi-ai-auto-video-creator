# -*- coding: utf-8 -*-
# Ravi Studio - कहानी डालो, तस्वीरों + आवाज़ वाला वीडियो पाओ (सब मुफ़्त)
#
# क्या नया है:
#  1. फोटो स्मार्ट तरीके से: Gemini कहानी समझकर हर दृश्य के लिए सटीक अंग्रेज़ी सर्च बनाता है,
#     Wikimedia/Pexels से उम्मीदवार लाता है, फिर Gemini सबसे सही फोटो चुनता है (पुरानी/बेमेल/लोगो हटाकर)।
#     न मिले तो AI फोटो (Pollinations, मुफ़्त)।
#  2. वीडियो बैकग्राउंड थ्रेड में बनता है: स्क्रीन बंद होने पर भी सर्वर पर बनता रहता है।
#     वापस आकर वही पेज खोलो, बने हुए दृश्य दोबारा नहीं बनते।
#  3. हर प्रोजेक्ट सर्वर की studio_data/ फ़ोल्डर में सेव रहता है। एडिट करने पर सिर्फ बदला हुआ दृश्य
#     दोबारा बनता है, वीडियो सर्वर पर ही रहता है, बार-बार डाउनलोड नहीं करना पड़ता।

import asyncio
import hashlib
import io
import json
import math
import os
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path

import requests
from PIL import Image, ImageOps

BASE = Path("studio_data")
BASE.mkdir(exist_ok=True)
UA = {"User-Agent": "RaviStudio/2.0 (personal video tool; contact: ravi-studio@example.com)"}
FPS = 25
EFFECTS = ["zoom_in", "pan_right", "zoom_out", "pan_left"]
BAD_WORDS = ["logo", "flag", "map", "icon", "signature", "diagram", "coat of arms",
             "stamp", "chart", "graph", "locator", "seal of", "wordmark", "banner"]


# ----------------------------------------------------------------------------
# फ़ाइल / प्रोजेक्ट हेल्पर
# ----------------------------------------------------------------------------
def pdir(pid):
    return BASE / pid


def _write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)


def load_proj(pid):
    p = pdir(pid) / "project.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else None


def save_proj(pid, data):
    _write_json(pdir(pid) / "project.json", data)


def get_status(pid):
    p = pdir(pid) / "status.json"
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return {}


def set_status(pid, **kw):
    s = get_status(pid)
    s.update(kw)
    _write_json(pdir(pid) / "status.json", s)


def list_projects():
    out = []
    for d in BASE.iterdir():
        f = d / "project.json"
        if f.exists():
            try:
                P = json.loads(f.read_text("utf-8"))
                out.append((f.stat().st_mtime, d.name, P.get("title", "")))
            except Exception:
                pass
    out.sort(reverse=True)
    return [(pid, t) for _, pid, t in out]


def new_project(story):
    pid = time.strftime("%m%d-%H%M%S")
    for sub in ("img", "aud", "clip"):
        (pdir(pid) / sub).mkdir(parents=True, exist_ok=True)
    title = re.sub(r"\s+", " ", story.strip())[:28]
    save_proj(pid, {"id": pid, "title": title, "story": story.strip(), "context": "",
                    "scenes": [], "music": "", "final_sig": ""})
    return pid


def dims(cfg):
    return (720, 1280) if cfg["aspect"] == "9:16" else (1280, 720)


# ----------------------------------------------------------------------------
# Gemini (मुफ़्त API key) - REST से, कोई SDK नहीं
# ----------------------------------------------------------------------------
def gem_json(prompt, key, model="gemini-flash-latest", tries=4):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3}}
    last = None
    for t in range(tries):
        try:
            r = requests.post(url, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                              json=body, timeout=90)
            if r.status_code in (429, 500, 503):
                last = f"HTTP {r.status_code}"
                time.sleep(7 * (t + 1))
                continue
            r.raise_for_status()
            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt)
            return json.loads(txt)
        except Exception as e:
            last = e
            time.sleep(3)
    raise RuntimeError(f"Gemini जवाब नहीं दे रहा (key/मॉडल का नाम जाँचें): {last}")


def split_story(story, words_per_scene):
    """कहानी को वाक्यों के हिसाब से लगभग बराबर दृश्यों में बाँटता है (टेक्स्ट जस का तस रहता है)।"""
    story = re.sub(r"\s+", " ", story.strip())
    sents = [s.strip() for s in re.split(r"(?<=[।!?\.])\s+", story) if s.strip()]
    parts = []
    for s in sents:
        if len(s.split()) > words_per_scene * 1.6:
            parts += [c for c in re.split(r"(?<=[,;:—])\s+", s) if c.strip()]
        else:
            parts.append(s)
    scenes, cur, n = [], [], 0
    for p in parts:
        w = len(p.split())
        if cur and n + w > words_per_scene * 1.25:
            scenes.append(" ".join(cur))
            cur, n = [], 0
        cur.append(p)
        n += w
        if n >= words_per_scene:
            scenes.append(" ".join(cur))
            cur, n = [], 0
    if cur:
        if scenes and n < words_per_scene * 0.4:
            scenes[-1] += " " + " ".join(cur)
        else:
            scenes.append(" ".join(cur))
    return scenes


PLAN_PROMPT = (
    "You choose photos for a Hindi video. Overall context: {ctx}\n"
    "For EACH numbered Hindi narration scene below return JSON exactly like:\n"
    '{"scenes":[{"i":1,"kind":"named","queries":["q1","q2","q3"],"ai_prompt":"..."}]}\n'
    "Rules:\n"
    "- kind = 'named' if the scene is about a specific real person, place, event or brand; else 'generic'.\n"
    "- queries: 3 English photo-search queries (2-6 words each), from most specific "
    "(full name + event/action + recent year, e.g. 'Virat Kohli batting 2023') to more generic. "
    "Keep proper names. For generic/emotional scenes use concrete visible things "
    "(e.g. 'cheering stadium crowd night'). Never write Hindi. Do not write the words photo or image.\n"
    "- ai_prompt: one vivid English sentence describing exactly what the viewer should see, "
    "realistic photo style, modern era, no text, no logos.\n"
    "Scenes:\n{listing}"
)


def plan_scenes(texts, cfg):
    k, m = cfg["gemini_key"], cfg["model"]
    head = " ".join(texts)[:1800]
    ctx = gem_json(
        'You help choose photos for a Hindi video. Read the script and reply JSON '
        '{"context": "<one English sentence: main subject, key people/places, time period '
        '(prefer modern visuals), and what kind of photos fit>"}\n\nScript:\n' + head, k, m
    ).get("context", "")
    out = {}
    for s in range(0, len(texts), 10):
        chunk = texts[s:s + 10]
        listing = "\n".join(f"{s + j + 1}. {t}" for j, t in enumerate(chunk))
        res = gem_json(PLAN_PROMPT.replace("{ctx}", ctx).replace("{listing}", listing), k, m)
        for it in res.get("scenes", []):
            try:
                out[int(it["i"])] = it
            except Exception:
                pass
    scenes = []
    for j, t in enumerate(texts):
        it = out.get(j + 1, {})
        q = [x.strip() for x in it.get("queries", []) if isinstance(x, str) and x.strip()][:3]
        scenes.append({"text": t, "kind": it.get("kind", "generic"), "queries": q or [ctx[:60]],
                       "ai_prompt": it.get("ai_prompt") or ctx, "img": "", "img_src": "",
                       "cands": [], "used": [], "audio": "", "dur": 0, "clip": "", "sig": "",
                       "effect": EFFECTS[j % len(EFFECTS)], "upl": ""})
    return ctx, scenes


# ----------------------------------------------------------------------------
# फोटो खोज
# ----------------------------------------------------------------------------
def _years(s):
    return [int(y) for y in re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", s)]


def search_wikimedia(q, min_year, aspect):
    r = requests.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=25, params={
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrsearch": q + " filetype:bitmap", "gsrlimit": 20, "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1280,
        "iiextmetadatafilter": "DateTimeOriginal|ImageDescription"})
    pages = (r.json().get("query") or {}).get("pages") or {}
    toks = [t for t in re.findall(r"\w+", q.lower()) if len(t) > 2]
    out = []
    for pg in pages.values():
        ii = (pg.get("imageinfo") or [None])[0]
        if not ii or ii.get("mime") not in ("image/jpeg", "image/png"):
            continue
        title = pg.get("title", "").replace("File:", "")
        low = title.lower()
        if any(b in low for b in BAD_WORDS):
            continue
        if ii.get("width", 0) < 900 or ii.get("height", 0) < 500:
            continue
        md = ii.get("extmetadata") or {}
        dt = (md.get("DateTimeOriginal") or {}).get("value", "")
        yrs = _years(low) + _years(dt)
        if yrs and max(yrs) < min_year:
            continue  # पुरानी फोटो
        desc = re.sub(r"<[^>]+>", " ", (md.get("ImageDescription") or {}).get("value", ""))[:200]
        hay = low + " " + desc.lower()
        score = sum(t in hay for t in toks) / max(1, len(toks))
        if aspect == "16:9" and ii["width"] > ii["height"]:
            score += 0.1
        url = ii.get("thumburl") or ii["url"]
        out.append({"url": url, "thumb": url, "title": title[:90], "source": "Wikimedia", "score": score})
    out.sort(key=lambda c: -c["score"])
    return out


def search_pexels(q, key, aspect):
    r = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": key}, timeout=25,
                     params={"query": q, "per_page": 10,
                             "orientation": "portrait" if aspect == "9:16" else "landscape"})
    r.raise_for_status()
    out = []
    for p in r.json().get("photos", []):
        out.append({"url": p["src"]["large2x"], "thumb": p["src"]["medium"],
                    "title": (p.get("alt") or q)[:90], "source": "Pexels", "score": 0.6})
    return out


def pick_best(text, ctx, cands, cfg):
    cands = cands[:8]
    if not cands:
        return None
    lst = "\n".join(f"{i}. {c['title']}" for i, c in enumerate(cands))
    try:
        res = gem_json(
            f"Video context: {ctx}\nNarration (Hindi): {text}\nCandidate photo titles:\n{lst}\n"
            "Pick the ONE photo that best fits the narration AND context (correct person/event, "
            "modern, not a logo/diagram/old black-and-white). "
            'Reply JSON {"best": <index>} or {"best": -1} if none is clearly suitable.',
            cfg["gemini_key"], cfg["model"], tries=2)
        b = int(res.get("best", -1))
        return cands[b] if 0 <= b < len(cands) else None
    except Exception:
        return cands[0] if cands[0]["score"] >= 0.5 else None


def fetch_image(url, dest):
    for _ in range(2):
        try:
            r = requests.get(url, headers=UA, timeout=40)
            r.raise_for_status()
            im = Image.open(io.BytesIO(r.content))
            im.load()
            if min(im.size) < 500:
                return False
            im.convert("RGB").save(dest, "JPEG", quality=92)
            return True
        except Exception:
            time.sleep(1.5)
    return False


def ai_image(prompt, dest, W, H):
    seed = random.randint(1, 99999)
    url = ("https://image.pollinations.ai/prompt/"
           + urllib.parse.quote(prompt + ", photorealistic, high detail, no text")
           + f"?width={W}&height={H}&nologo=true&seed={seed}")
    for _ in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=120)
            if r.ok and r.headers.get("content-type", "").startswith("image"):
                Image.open(io.BytesIO(r.content)).convert("RGB").save(dest, "JPEG", quality=92)
                return True
        except Exception:
            pass
        time.sleep(4)
    return False


def _dedupe(pool):
    seen, out = set(), []
    for c in sorted(pool, key=lambda c: -c["score"]):
        if c["url"] not in seen:
            seen.add(c["url"])
            out.append(c)
    return out


def find_image(pid, i, sc, ctx, cfg):
    """दृश्य के लिए फोटो ढूँढकर सेव करता है। लौटाता है: 'real' / 'ai' / 'none'."""
    W, H = dims(cfg)
    name = f"{i:02d}.jpg"
    dest = pdir(pid) / "img" / name
    used = sc.setdefault("used", [])
    pool = []
    if cfg["img_mode"] != "ai":
        srcs = ["wiki", "pexels"] if sc.get("kind") == "named" else ["pexels", "wiki"]
        for src in srcs:
            if src == "pexels" and not cfg.get("pexels_key"):
                continue
            for q in sc.get("queries", []):
                try:
                    c = (search_wikimedia(q, cfg["min_year"], cfg["aspect"]) if src == "wiki"
                         else search_pexels(q, cfg["pexels_key"], cfg["aspect"]))
                except Exception:
                    c = []
                c = [x for x in c if x["url"] not in used]
                if not c:
                    continue
                pool += c
                if cfg.get("ai_pick") and cfg.get("gemini_key"):
                    best = pick_best(sc["text"], ctx, c, cfg)
                else:
                    best = c[0] if c[0]["score"] >= 0.4 else None
                if best and fetch_image(best["url"], dest):
                    used.append(best["url"])
                    sc.update(img=name, img_src=f"{best['source']}: {best['title']}")
                    sc["cands"] = _dedupe(pool)[:9]
                    return "real"
    sc["cands"] = _dedupe(pool)[:9]
    if cfg["img_mode"] in ("real_ai", "ai") and ai_image(sc.get("ai_prompt") or ctx, dest, W, H):
        sc.update(img=name, img_src="AI फोटो")
        return "ai"
    Image.new("RGB", (W, H), (22, 26, 44)).save(dest, "JPEG")
    sc.update(img=name, img_src="खाली (फोटो नहीं मिली, नीचे से चुनो/अपलोड करो)")
    return "none"


# ----------------------------------------------------------------------------
# आवाज़ और वीडियो (ffmpeg)
# ----------------------------------------------------------------------------
def make_audio(text, dest, voice, rate):
    dest = Path(dest)
    try:
        import edge_tts

        async def go():
            await edge_tts.Communicate(text, voice, rate=f"{int(rate):+d}%").save(str(dest))

        for _ in range(3):
            try:
                asyncio.run(go())
                if dest.exists() and dest.stat().st_size > 1000:
                    return "edge"
            except Exception:
                time.sleep(2)
    except ImportError:
        pass
    try:
        from gtts import gTTS
        gTTS(text=text, lang="hi").save(str(dest))
        return "gtts"
    except Exception as e:
        raise RuntimeError(f"आवाज़ नहीं बन पाई (edge-tts और gTTS दोनों फेल): {e}")


def media_dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def run_ff(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg गड़बड़: " + (r.stderr or "")[-400:])


def render_clip(img, aud, out, W, H, effect, dur):
    PW, PH = int(W * 1.5), int(H * 1.5)
    prep = Path(str(out) + ".prep.jpg")
    im = Image.open(img).convert("RGB")
    ImageOps.fit(im, (PW, PH), Image.LANCZOS, centering=(0.5, 0.4)).save(prep, quality=92)
    n = int(dur * FPS) + 2
    cy = "y='ih/2-(ih/zoom/2)'"
    z = {"zoom_in": f"z='1+0.22*on/{n}':x='iw/2-(iw/zoom/2)':{cy}",
         "zoom_out": f"z='1.22-0.22*on/{n}':x='iw/2-(iw/zoom/2)':{cy}",
         "pan_right": f"z='1.15':x='(iw-iw/zoom)*on/{n}':{cy}",
         "pan_left": f"z='1.15':x='(iw-iw/zoom)*(1-on/{n})':{cy}"}.get(effect)
    z = z or f"z='1+0.22*on/{n}':x='iw/2-(iw/zoom/2)':{cy}"
    vf = (f"zoompan={z}:d={n}:s={W}x{H}:fps={FPS},"
          f"fade=t=in:st=0:d=0.3,fade=t=out:st={max(0, dur - 0.3):.2f}:d=0.3,format=yuv420p")
    try:
        run_ff(["ffmpeg", "-y", "-i", str(prep), "-i", str(aud), "-vf", vf, "-af", "apad",
                "-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
                "-r", str(FPS), "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
                "-movflags", "+faststart", str(out)])
    finally:
        prep.unlink(missing_ok=True)


def concat_clips(clips, out, music=None):
    out = Path(out)
    lst = out.parent / "list.txt"
    lst.write_text("".join(f"file '{Path(c).resolve()}'\n" for c in clips), "utf-8")
    tmp = out.with_name("tmp_concat.mp4")
    run_ff(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(tmp)])
    if music and Path(music).exists():
        run_ff(["ffmpeg", "-y", "-i", str(tmp), "-stream_loop", "-1", "-i", str(music),
                "-filter_complex",
                "[1:a]volume=0.15[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]",
                "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                str(out)])
        tmp.unlink(missing_ok=True)
    else:
        os.replace(tmp, out)


def _fsig(path):
    p = Path(path)
    return f"{p.stat().st_size}-{p.stat().st_mtime_ns}" if p.exists() else "0"


def clip_sig(pid, sc, W, H):
    d = pdir(pid)
    return (f"{W}x{H}|{sc['effect']}|{_fsig(d / 'img' / sc['img']) if sc['img'] else '0'}"
            f"|{_fsig(d / 'aud' / sc['audio']) if sc['audio'] else '0'}")


# ----------------------------------------------------------------------------
# मुख्य पाइपलाइन (बैकग्राउंड थ्रेड में चलती है; बने हुए हिस्से दोबारा नहीं बनते)
# ----------------------------------------------------------------------------
def pipeline(pid, cfg):
    try:
        P = load_proj(pid)
        W, H = dims(cfg)
        d = pdir(pid)
        set_status(pid, running=True, error=None, stage="शुरू हो रहा है...", done=0, total=1)

        if not P["scenes"]:
            set_status(pid, stage="कहानी समझकर दृश्य और फोटो-सर्च बना रहा है (AI)...")
            texts = split_story(P["story"], cfg["words"])
            if not texts:
                raise RuntimeError("कहानी खाली है।")
            P["context"], P["scenes"] = plan_scenes(texts, cfg)
            save_proj(pid, P)

        scenes = P["scenes"]
        n = len(scenes)
        total = n * 3 + 1
        step = 0
        for i, sc in enumerate(scenes):
            tag = f"दृश्य {i + 1}/{n}"
            if not (sc["img"] and (d / "img" / sc["img"]).exists()):
                set_status(pid, stage=f"{tag}: फोटो खोज रहा है...", done=step, total=total)
                find_image(pid, i, sc, P["context"], cfg)
                save_proj(pid, P)
            step += 1
            if not (sc["audio"] and (d / "aud" / sc["audio"]).exists()):
                set_status(pid, stage=f"{tag}: आवाज़ बना रहा है...", done=step, total=total)
                name = f"{i:02d}.mp3"
                make_audio(sc["text"], d / "aud" / name, cfg["voice"], cfg["rate"])
                sc["audio"] = name
                sc["dur"] = round(media_dur(d / "aud" / name) + 0.5, 2)
                save_proj(pid, P)
            step += 1
            sig = clip_sig(pid, sc, W, H)
            if not (sc["clip"] and (d / "clip" / sc["clip"]).exists() and sc["sig"] == sig):
                set_status(pid, stage=f"{tag}: क्लिप बना रहा है...", done=step, total=total)
                name = f"{i:02d}.mp4"
                render_clip(d / "img" / sc["img"], d / "aud" / sc["audio"], d / "clip" / name,
                            W, H, sc["effect"], sc["dur"])
                sc["clip"], sc["sig"] = name, sig
                save_proj(pid, P)
            step += 1

        music = d / P["music"] if P.get("music") else None
        fsig = hashlib.md5(("|".join(s["sig"] for s in scenes)
                            + (_fsig(music) if music else "")).encode()).hexdigest()
        if P.get("final_sig") != fsig or not (d / "final.mp4").exists():
            set_status(pid, stage="सारे क्लिप जोड़ रहा है...", done=step, total=total)
            concat_clips([d / "clip" / s["clip"] for s in scenes], d / "final.mp4", music)
            P["final_sig"] = fsig
            save_proj(pid, P)
        set_status(pid, running=False, stage="तैयार ✅", done=total, total=total)
    except Exception as e:
        set_status(pid, running=False, error=str(e)[:600])


# ============================================================================
# ===== UI =====
# ============================================================================
import streamlit as st  # noqa: E402

st.set_page_config(page_title="Ravi Studio", page_icon="🎬", layout="centered")


@st.cache_resource
def _jobs():
    return {}


JOBS = _jobs()


def secret(name):
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""


def is_running(pid):
    t = JOBS.get(pid)
    return bool(t and t.is_alive())


def start_job(pid, cfg):
    if not is_running(pid):
        t = threading.Thread(target=pipeline, args=(pid, cfg), daemon=True)
        t.start()
        JOBS[pid] = t


# ---------------- साइडबार ----------------
with st.sidebar:
    st.header("⚙️ सेटिंग")
    gem = st.text_input("Gemini API key (मुफ़्त)", value=secret("GEMINI_API_KEY"), type="password",
                        help="aistudio.google.com से मुफ़्त मिलती है")
    pex = st.text_input("Pexels API key (वैकल्पिक, मुफ़्त)", value=secret("PEXELS_API_KEY"),
                        type="password", help="pexels.com/api से मुफ़्त; आम (बिना नाम की) फोटो के लिए अच्छी")
    aspect_s = st.radio("वीडियो का आकार", ["16:9 (YouTube)", "9:16 (Shorts/Reels)"])
    voice = st.selectbox("आवाज़", ["hi-IN-SwaraNeural", "hi-IN-MadhurNeural"],
                         format_func=lambda v: "स्वरा (महिला)" if "Swara" in v else "मधुर (पुरुष)")
    rate = st.slider("बोलने की रफ़्तार (%)", -30, 30, 0)
    sec = st.slider("हर दृश्य लगभग कितने सेकंड", 6, 16, 10)
    mode_s = st.radio("फोटो", ["असली फोटो, न मिले तो AI", "सिर्फ असली फोटो", "हमेशा AI फोटो"])
    ai_pick = st.checkbox("AI से सबसे सही फोटो चुनवाओ", True)
    min_year = st.number_input("इस साल से पुरानी फोटो नहीं", 1990, 2025, 2008)
    model = st.text_input("Gemini मॉडल", "gemini-flash-latest")

cfg = dict(gemini_key=gem, pexels_key=pex, model=model.strip() or "gemini-flash-latest",
           aspect="9:16" if aspect_s.startswith("9") else "16:9", voice=voice, rate=rate,
           words=max(12, int(sec * 2.4)),
           img_mode={"असली फोटो, न मिले तो AI": "real_ai", "सिर्फ असली फोटो": "real",
                     "हमेशा AI फोटो": "ai"}[mode_s],
           ai_pick=ai_pick, min_year=int(min_year))

projects = list_projects()
NEW = "➕ नया प्रोजेक्ट"
opts = [NEW] + [p for p, _ in projects]
titles = dict(projects)
cur = st.query_params.get("p")
sel = st.sidebar.selectbox("प्रोजेक्ट", opts, index=opts.index(cur) if cur in opts else 0,
                           format_func=lambda x: x if x == NEW else f"{x} · {titles.get(x, '')}")
if sel != (cur if cur in opts else NEW):
    if sel == NEW:
        st.query_params.clear()
    else:
        st.query_params["p"] = sel
    st.rerun()
pid = None if sel == NEW else sel

st.title("🎬 Ravi Studio")

# ---------------- नया प्रोजेक्ट ----------------
if pid is None:
    story = st.text_area("अपनी कहानी / स्क्रिप्ट यहाँ डालो (हिंदी)", height=260,
                         placeholder="सिर्फ कहानी लिखो। फोटो, दृश्य और keywords ऐप खुद तय करेगा।")
    if st.button("🎬 वीडियो बनाओ", type="primary", use_container_width=True):
        if not story.strip():
            st.warning("पहले कहानी डालो।")
        elif not gem:
            st.warning("साइडबार में Gemini API key डालो (मुफ़्त है), इसके बिना फोटो सटीक नहीं आएँगी।")
        else:
            pid = new_project(story)
            start_job(pid, cfg)
            st.query_params["p"] = pid
            st.rerun()
    st.stop()

# ---------------- मौजूदा प्रोजेक्ट ----------------
P = load_proj(pid)
if not P:
    st.error("यह प्रोजेक्ट नहीं मिला।")
    st.stop()
S = get_status(pid)
running = is_running(pid)
d = pdir(pid)
W, H = dims(cfg)

if running:
    st.progress(min(1.0, S.get("done", 0) / max(1, S.get("total", 1))), text=S.get("stage", "..."))
    st.info("📱 स्क्रीन बंद हो जाए तो भी वीडियो सर्वर पर बनता रहेगा। वापस आकर यही पेज खोलो, "
            "बने हुए दृश्य दोबारा नहीं बनेंगे।")
    time.sleep(3)
    st.rerun()

if S.get("error"):
    st.error(S["error"])
if S.get("running") and not running:
    st.warning("बनना बीच में रुक गया था (सर्वर रीस्टार्ट)। नीचे 'आगे बढ़ाओ' दबाओ।")

fin = d / "final.mp4"
done_all = fin.exists() and P.get("scenes") and all(s.get("clip") for s in P["scenes"])
if not done_all or S.get("error"):
    if st.button("▶️ वहीं से आगे बढ़ाओ", type="primary", use_container_width=True):
        if not gem:
            st.warning("Gemini API key डालो।")
        else:
            start_job(pid, cfg)
            st.rerun()

if fin.exists():
    st.video(str(fin))
    with open(fin, "rb") as f:
        st.download_button("📥 वीडियो डाउनलोड करो", f, file_name=f"ravi_{pid}.mp4",
                           mime="video/mp4", use_container_width=True)
    st.caption("वीडियो सर्वर पर सेव है। नीचे एडिट करके दोबारा जोड़ने पर यही अपडेट होगा।")

# ---------------- एडिटर ----------------
if P.get("scenes"):
    st.subheader("✏️ एडिट")
    mus = st.file_uploader("बैकग्राउंड म्यूज़िक (वैकल्पिक)", type=["mp3", "wav", "m4a"], key=f"mus{pid}")
    if mus is not None:
        sig = f"{mus.name}-{mus.size}"
        if P.get("music_sig") != sig:
            (d / "music.bin").write_bytes(mus.getvalue())
            P["music"], P["music_sig"] = "music.bin", sig
            save_proj(pid, P)
            st.success("म्यूज़िक जुड़ गया, नीचे 'दोबारा जोड़ो' दबाओ।")

    edits = {}
    for i, sc in enumerate(P["scenes"]):
        with st.expander(f"दृश्य {i + 1} · {sc['text'][:34]}…"):
            ip = d / "img" / sc["img"] if sc.get("img") else None
            if ip and ip.exists():
                st.image(str(ip), caption=sc.get("img_src", ""))
            t = st.text_area("बोला जाने वाला टेक्स्ट", sc["text"], key=f"t{pid}{i}")
            e = st.selectbox("एनिमेशन", EFFECTS, index=EFFECTS.index(sc.get("effect", "zoom_in")),
                             key=f"e{pid}{i}")
            edits[i] = (t.strip(), e)

            c1, c2 = st.columns(2)
            if c1.button("🔄 नई फोटो खोजो", key=f"s{pid}{i}", use_container_width=True):
                if not gem:
                    st.warning("Gemini API key डालो।")
                else:
                    with st.spinner("खोज रहा हूँ..."):
                        find_image(pid, i, sc, P["context"], cfg)
                        save_proj(pid, P)
                    st.rerun()
            if c2.button("🤖 AI फोटो बनाओ", key=f"a{pid}{i}", use_container_width=True):
                with st.spinner("AI फोटो बन रही है (आधा मिनट तक)..."):
                    if ai_image(sc.get("ai_prompt") or P["context"], d / "img" / f"{i:02d}.jpg", W, H):
                        sc.update(img=f"{i:02d}.jpg", img_src="AI फोटो")
                        save_proj(pid, P)
                        st.rerun()
                    else:
                        st.error("AI फोटो नहीं बनी, थोड़ी देर बाद फिर कोशिश करो।")

            up = st.file_uploader("या अपनी फोटो लगाओ", type=["jpg", "jpeg", "png", "webp"], key=f"u{pid}{i}")
            if up is not None and sc.get("upl") != f"{up.name}-{up.size}":
                Image.open(io.BytesIO(up.getvalue())).convert("RGB").save(d / "img" / f"{i:02d}.jpg", quality=92)
                sc.update(img=f"{i:02d}.jpg", img_src="अपनी फोटो", upl=f"{up.name}-{up.size}")
                save_proj(pid, P)
                st.rerun()

            cands = sc.get("cands", [])[:6]
            if cands:
                st.caption("दूसरी फोटो चुनो:")
                cols = st.columns(3)
                for k, c in enumerate(cands):
                    with cols[k % 3]:
                        st.image(c["thumb"], use_container_width=True)
                        if st.button("लगाओ", key=f"c{pid}{i}{k}", use_container_width=True):
                            with st.spinner("लगा रहा हूँ..."):
                                if fetch_image(c["url"], d / "img" / f"{i:02d}.jpg"):
                                    sc.setdefault("used", []).append(c["url"])
                                    sc.update(img=f"{i:02d}.jpg", img_src=f"{c['source']}: {c['title']}")
                                    save_proj(pid, P)
                                    st.rerun()
                                else:
                                    st.error("यह फोटो नहीं आई।")

    if st.button("🔁 बदलाव लागू करके वीडियो दोबारा जोड़ो", type="primary", use_container_width=True):
        for i, (t, e) in edits.items():
            sc = P["scenes"][i]
            if t and t != sc["text"]:
                sc["text"], sc["audio"] = t, ""
            sc["effect"] = e
        save_proj(pid, P)
        start_job(pid, cfg)
        st.rerun()

with st.sidebar:
    st.divider()
    if st.checkbox("🗑️ यह प्रोजेक्ट हटाना है"):
        if st.button("पक्का हटाओ"):
            shutil.rmtree(d, ignore_errors=True)
            st.query_params.clear()
            st.rerun()
