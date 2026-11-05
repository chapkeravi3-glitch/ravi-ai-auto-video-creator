
# -*- coding: utf-8 -*-
"""
Ravi Boost - आपके Ravi Studio ऐप में बिना उसका कोड बदले सुधार जोड़ने वाली फ़ाइल।

यह फ़ाइल ऐप की मुख्य .py फ़ाइल के साथ उसी फ़ोल्डर में रखें। मुख्य फ़ाइल के सबसे ऊपर
RAVI-BOOST-LOADER वाली 6 लाइनें जुड़ी हों। ऐप चलते समय यह मुख्य फ़ाइल की प्रति में
सुधार जोड़कर ravi_patched_app.py बनाती और उसे चलाती है। कुछ बिगड़े तो ऊपर की 6 लाइनें
हटा दें, ऐप पहले जैसा चलेगा।
"""
import hashlib
import os
import re
import runpy

LOADER_BEGIN = "# --- RAVI-BOOST-LOADER-BEGIN ---"
LOADER_END = "# --- RAVI-BOOST-LOADER-END ---"
MARK = "\n# ==== UI ====\n"

OVERRIDE_CODE = r'''# ===================== RAVI BOOST: अपने-आप जुड़ा हिस्सा =====================
import html as _bh

PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic")


# ---- 1) Gemini: व्यस्त (503/429) हो तो रुककर दोबारा कोशिश करता है
def _gem_call(key, parts, models, rounds=3):
    errors = []
    url = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
    for rnd in range(rounds):
        busy = False
        for model in models:
            for use_json in (True, False):
                try:
                    body = {"contents": [{"parts": parts}]}
                    if use_json:
                        body["generationConfig"] = {"responseMimeType": "application/json"}
                    r = requests.post(
                        url.format(model),
                        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                        json=body, timeout=90,
                    )
                except Exception as e:
                    errors.append(f"{model}: {str(e)[:80]}")
                    busy = True
                    break
                if r.status_code == 200:
                    cands = r.json().get("candidates") or []
                    if cands:
                        ps = cands[0].get("content", {}).get("parts", [])
                        text = "".join(p.get("text", "") for p in ps)
                        if text.strip():
                            return text, ""
                    errors.append(f"{model}: खाली जवाब")
                    break
                errors.append(f"{model} {r.status_code}: {r.text[:120]}")
                if r.status_code in (429, 500, 502, 503, 504):
                    busy = True
                    break
                if r.status_code == 400 and use_json and "mime" in r.text.lower():
                    continue
                if r.status_code in (400, 401, 403) and "key" in r.text.lower():
                    return "", " | ".join(errors)[-400:]
                break
        if not busy:
            break
        time.sleep(6 * (rnd + 1))
    return "", " | ".join(errors[-6:])[:500]


def gemini_generate(key, prompt, models):
    return _gem_call(key, [{"text": prompt}], models, rounds=3)


def gemini_generate_parts(key, parts, models):
    return _gem_call(key, parts, models, rounds=2)


# ---- 2) Bing फोटो-खोज (बिना key) और स्रोतों की सूची
def bing_image_urls(query, err=None, timelimit=None):
    ages = {"d": 1440, "w": 10080, "m": 43200, "y": 525600}
    params = {"q": query, "form": "HDRSC2", "first": 1, "count": 35, "mmasync": 1}
    if timelimit in ages:
        params["qft"] = f"+filterui:age-lt{ages[timelimit]}"
    try:
        r = requests.get(
            "https://www.bing.com/images/async", params=params,
            headers={"User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
                                   "(KHTML, like Gecko) Chrome/120 Mobile Safari/537.36"},
            timeout=25,
        )
        r.raise_for_status()
        out = []
        for m in re.finditer(r'\sm="(\{.*?\})"', r.text):
            try:
                d = json.loads(_bh.unescape(m.group(1)))
            except Exception:
                continue
            u = d.get("murl")
            if u:
                out.append((u, f"{d.get('t', '')} {d.get('desc', '')}"))
        return out
    except Exception as e:
        _note(err, f"Bing फोटो: {type(e).__name__}: {str(e)[:80]}")
        return []


def make_sources(query, px_key, web_on, tl):
    srcs = []
    if tl:
        srcs.append(("Bing समाचार", lambda e: bing_news_urls(query, e, tl)))
        if web_on:
            srcs.append(("समाचार-फोटो", lambda e: ddg_news_urls(query, e, tl)))
            srcs.append(("इंटरनेट (ताज़ा)", lambda e: web_urls(query, e, tl)))
            srcs.append(("Bing फोटो", lambda e: bing_image_urls(query, e, tl)))
        srcs.append(("Wikimedia (नई)", lambda e: wikimedia_urls(query, e, WIKI_YEARS.get(tl, 2))))
        if px_key:
            srcs.append(("Pixabay (नई)", lambda e: pixabay_urls(query, px_key, e, latest=True)))
    else:
        if px_key:
            srcs.append(("Pixabay", lambda e: pixabay_urls(query, px_key, e)))
        srcs.append(("Wikimedia", lambda e: wikimedia_urls(query, e)))
        if web_on:
            srcs.append(("इंटरनेट", lambda e: web_urls(query, e)))
            srcs.append(("Bing फोटो", lambda e: bing_image_urls(query, e)))
        srcs.append(("Openverse", lambda e: openverse_urls(query, e)))
    return srcs


# ---- 3) AI तस्वीर: पहले से बड़ी और साफ़ (HD के क़रीब)
def ai_image(prompt, save_path, size, err=None):
    if len(prompt.strip()) < 15:
        return False
    w, h = size
    scale = 1536 / max(w, h)
    w = max(256, int(w * scale) // 8 * 8)
    h = max(256, int(h * scale) // 8 * 8)
    full = (
        f"{prompt}, ultra sharp, highly detailed, realistic photograph, "
        "natural lighting, no text, no watermark"
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
            params = {"width": w, "height": h, "nologo": "true",
                      "seed": random.randint(1, 1000000)}
            if model:
                params["model"] = model
            r = requests.get(
                "https://image.pollinations.ai/prompt/" + quote(full[:600]),
                params=params, headers=HEADERS, timeout=120,
            )
            _AI_GATE["t"] = time.time()
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
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


# ---- 4) अपनी फोटो-लाइब्रेरी: photos/<विषय>/ फ़ोल्डर
def boost_library():
    base = os.path.join(_APP_DIR, "photos")
    lib = {}
    try:
        if os.path.isdir(base):
            for name in sorted(os.listdir(base)):
                d = os.path.join(base, name)
                if not os.path.isdir(d):
                    continue
                files = sorted(
                    os.path.join(r_, f_) for r_, _d, fs in os.walk(d) for f_ in fs
                    if f_.lower().endswith(PHOTO_EXT)
                )
                if not files:
                    continue
                kws = [w.lower() for w in re.split(r"[\s_\-]+", name) if len(w) >= 3]
                extra = ""
                kp = os.path.join(d, "keywords.txt")
                if os.path.exists(kp):
                    try:
                        with open(kp, encoding="utf-8") as f_:
                            extra = f_.read().lower()
                    except Exception:
                        extra = ""
                lib[name] = {"files": files, "kw": kws, "extra": extra}
    except Exception:
        pass
    return lib


def boost_match_folder(lib, blob):
    blob = (blob or "").lower()
    best, best_score = None, 0
    for name, info in lib.items():
        kws = info["kw"]
        hits = sum(1 for w in kws if w in blob)
        for ph in re.split(r"[,\n]+", info["extra"]):
            ph = ph.strip()
            if len(ph) >= 2 and ph in blob:
                hits += 2
        need = 1 if len(kws) <= 1 else 2
        if hits >= need and hits > best_score:
            best, best_score = name, hits
    return best


def boost_library_photo(blob, save_path, used, log, allow_reuse=False):
    lib = boost_library()
    if not lib:
        return None
    name = boost_match_folder(lib, blob)
    if not name:
        return None
    files = lib[name]["files"]
    fresh = [f_ for f_ in files if f_ not in used]
    pool = list(fresh or (files if allow_reuse else []))
    random.shuffle(pool)
    for f_ in pool[:6]:
        try:
            with Image.open(f_) as img:
                rgb = to_rgb(img)
            if rgb.width < 200 or rgb.height < 200:
                continue
            rgb.thumbnail((2400, 2400))
            rgb.save(save_path, "JPEG", quality=92)
            used.add(f_)
            log.append(f"📚 आपकी लाइब्रेरी: {name}/{os.path.basename(f_)}")
            return f"आपकी लाइब्रेरी: {name}"
        except Exception:
            continue
    return None


# ---- 5) बेहतर फोटो-खोज: लाइब्रेरी -> पुराना तरीका -> नरम खोज (बिना शीर्षक-मेल की शर्त)
_orig_smart_photo = smart_photo


def boost_lenient(queries, save_path, px_key, web_on, anchor, used, log):
    seen, qs = set(), []
    for q in list(queries or []) + ([anchor] if anchor else []):
        toks = [w for w in re.findall(r"[A-Za-z]+", q or "")
                if len(w) > 2 and w.lower() not in STOPWORDS]
        for cand in (" ".join(toks[:4]), " ".join(toks[:2]), toks[0] if toks else ""):
            if cand and cand.lower() not in seen:
                seen.add(cand.lower())
                qs.append(cand)
    for q in qs[:4]:
        srcs = []
        if px_key:
            srcs.append(("Pixabay", lambda e, q=q: pixabay_urls(q, px_key, e)))
        srcs.append(("Wikimedia", lambda e, q=q: wikimedia_urls(q, e)))
        srcs.append(("Openverse", lambda e, q=q: openverse_urls(q, e)))
        if web_on:
            srcs.append(("Bing फोटो", lambda e, q=q: bing_image_urls(q, e)))
            srcs.append(("इंटरनेट", lambda e, q=q: web_urls(q, e)))
        for name, fn in srcs:
            errs = []
            cands = fn(errs)
            ok = try_download(cands[:15], save_path, used, set(), max_tries=6)
            log.append(
                f"(नरम खोज) '{q}' → {name}: {len(cands)} मिलीं"
                + (" ✅ ली गई" if ok else "")
                + (f" | {errs[0]}" if errs else "")
            )
            if ok:
                return name + " (नरम खोज)", q
    return None, None


_SUBJ_CACHE = {}
_NAME_SKIP = {"The", "This", "That", "These", "Those", "It", "He", "She", "They", "His", "Her",
              "In", "On", "At", "And", "But", "When", "After", "Before", "While", "Then",
              "There", "Here", "What", "Who", "How", "Why", "Today", "Yesterday", "One", "Two",
              "Three", "A", "An", "Of", "To", "For", "With", "From", "By", "As", "If", "So",
              "We", "You", "I", "Our", "Their", "Its", "Was", "Were", "Is", "Are"}


def boost_entities(text):
    """अंग्रेज़ी वाक्य से नाम निकालता है (खिलाड़ी, टीम, जगह): बड़े अक्षर से शुरू होने वाले शब्द।"""
    found = []
    for m in re.finditer(r"[A-Z][\w'\-\.]*(?:\s+[A-Z][\w'\-\.]*)*", text or ""):
        toks = [t for t in re.findall(r"[A-Za-z]+", m.group(0)) if t not in _NAME_SKIP]
        name = " ".join(toks).strip()
        if len(name) >= 3 and name.lower() not in STOPWORDS:
            found.append(name)
    uniq = sorted(set(found), key=lambda x: (-len(x.split()), -len(x)))
    return uniq[:4]


def boost_subject_photo(names, save_path, px_key, web_on, used, log, tl, modern):
    """नाम (जैसे खिलाड़ी/टीम) से ढेर सारी फोटो जुटाकर हर बार एक नई, अलग फोटो लेता है।"""
    done = set()
    for name in names[:3]:
        key = name.lower()
        if key in done:
            continue
        done.add(key)
        ck = (key, tl, bool(px_key), bool(web_on))
        cands = _SUBJ_CACHE.get(ck)
        if cands is None:
            cands, seen = [], set()
            fetchers = [("Wikimedia", lambda e, n_=name: wikimedia_urls(n_, e)),
                        ("Openverse", lambda e, n_=name: openverse_urls(n_, e))]
            if px_key:
                fetchers.append(("Pixabay", lambda e, n_=name: pixabay_urls(n_, px_key, e)))
            if web_on:
                fetchers.append(("Bing फोटो", lambda e, n_=name: bing_image_urls(n_, e)))
                fetchers.append(("इंटरनेट", lambda e, n_=name: web_urls(n_, e)))
            counts = []
            for label, fn in fetchers:
                try:
                    got = fn([])
                except Exception:
                    got = []
                counts.append(f"{label} {len(got)}")
                for url, text in got:
                    if url and url not in seen:
                        seen.add(url)
                        cands.append((url, text or ""))
            _SUBJ_CACHE[ck] = cands
            log.append(f"विषय-खोज '{name}': " + ", ".join(counts))
        toks = {t.lower() for t in re.findall(r"[A-Za-z]{3,}", name)}
        pref = [(u, t) for u, t in cands if u not in used and score_text(t, toks) >= 1]
        ok = try_download(pref, save_path, used, set(), max_tries=8, modern=modern)
        log.append(f"विषय-खोज '{name}': {len(pref)} नई फोटो उपलब्ध" + (" ✅ ली गई" if ok else ""))
        if ok:
            return f"विषय की फोटो: {name}", name
    return None, None


def smart_photo(queries, save_path, folder, tag, px_key, web_on, anchor,
                modern, tl, gem, scene_en, topic, exclude, log, used, n=5):
    blob = " ".join(list(queries or [])) + " " + (anchor or "") + " " + (scene_en or "")
    try:
        hit = boost_library_photo(blob, save_path, used, log)
        if hit:
            return hit, (queries[0] if queries else "")
    except Exception as e:
        log.append(f"लाइब्रेरी में समस्या: {str(e)[:80]}")
    try:
        src, q = _orig_smart_photo(queries, save_path, folder, tag, px_key, web_on,
                                   anchor, modern, tl, gem, scene_en, topic,
                                   exclude, log, used, n)
        if src:
            return src, q
    except Exception as e:
        log.append(f"खोज में समस्या: {str(e)[:80]}")
    try:
        names = boost_entities(scene_en) + ([anchor] if anchor else []) + boost_entities(topic)
        names = [n_ for i_, n_ in enumerate(names) if n_ and n_ not in names[:i_]]
        src, q = boost_subject_photo(names, save_path, px_key, web_on, used, log, tl, modern)
        if src:
            return src, q
    except Exception as e:
        log.append(f"विषय-खोज में समस्या: {str(e)[:80]}")
    try:
        lenient_on = bool(S.get("lenient", False))
    except Exception:
        lenient_on = False
    if lenient_on:
        try:
            src, q = boost_lenient(queries, save_path, px_key, web_on, anchor, used, log)
            if src:
                return src, q
        except Exception as e:
            log.append(f"नरम खोज में समस्या: {str(e)[:80]}")
    else:
        log.append("मेल खाती फोटो नहीं मिली (नरम खोज बंद है, इसलिए ग़लत फोटो नहीं लगाई)")
    try:
        hit = boost_library_photo(blob + " " + (topic or ""), save_path, used, log, allow_reuse=True)
        if hit:
            return hit, (queries[0] if queries else "")
    except Exception:
        pass
    return None, None


# ---- 6) कैप्शन: बोले जा रहे शब्द पीले होते जाते हैं
_orig_write_ass = write_ass
_DLG = re.compile(
    r"^(Dialogue: 0,)(\d+:\d\d:\d\d\.\d\d),(\d+:\d\d:\d\d\.\d\d),"
    r"(Default,,0,0,0,,)(\{[^}]*\})?(.*)$"
)


def _ass_cs(t):
    h_, m_, rest = t.split(":")
    s_, c_ = rest.split(".")
    return ((int(h_) * 60 + int(m_)) * 60 + int(s_)) * 100 + int(c_)


def _boost_karaoke(txt):
    lines, style_ok = [], False
    for line in txt.split("\n"):
        if line.startswith("Style: Default,") and "&H00FFFFFF,&H000000FF," in line:
            line = line.replace("&H00FFFFFF,&H000000FF,", "&H0000E0FF,&H00FFFFFF,", 1)
            style_ok = True
        lines.append(line)
    if not style_ok:
        return txt
    out = []
    for line in lines:
        m = _DLG.match(line)
        if m:
            head, a, b, mid, tag, body = m.groups()
            words = body.split()
            if words:
                total = max(len(words), _ass_cs(b) - _ass_cs(a))
                weights = [len(w) + 1 for w in words]
                wsum = float(sum(weights))
                parts = []
                for w, wt in zip(words, weights):
                    cs = max(1, int(total * wt / wsum))
                    parts.append("{\\k%d}%s" % (cs, w))
                body = " ".join(parts)
                line = f"{head}{a},{b},{mid}{tag or ''}{body}"
        out.append(line)
    return "\n".join(out)


def write_ass(*args, **kwargs):
    ok = _orig_write_ass(*args, **kwargs)
    try:
        on = S.get("karaoke", True)
    except Exception:
        on = True
    if not ok or not on:
        return ok
    try:
        path = args[0]
        with open(path, encoding="utf-8") as f_:
            txt = f_.read()
        txt2 = _boost_karaoke(txt)
        if txt2 != txt:
            with open(path, "w", encoding="utf-8") as f_:
                f_.write(txt2)
    except Exception:
        pass
    return ok


# ---- 7) स्क्रीन के नए हिस्से
def _lib_thumb(path):
    try:
        d = os.path.join(tempfile.gettempdir(), "ravi_libthumbs")
        os.makedirs(d, exist_ok=True)
        tp = os.path.join(d, hashlib.md5(path.encode()).hexdigest() + ".jpg")
        if not os.path.exists(tp):
            with Image.open(path) as im:
                im = to_rgb(im)
                im.thumbnail((240, 240))
                im.save(tp, "JPEG", quality=80)
        return tp
    except Exception:
        return None


def boost_photo_tools(P_, sel, scenes):
    n = len(scenes)
    ctr_key = f"bulk_ctr_{P_['id']}"
    ctr = st.session_state.get(ctr_key, 0)

    with st.expander("📥 एक साथ कई फोटो डालें (दृश्यों में क्रम से)"):
        files = st.file_uploader(
            "गैलरी से कई फोटो चुनें", accept_multiple_files=True,
            key=f"bulk_{P_['id']}_{ctr}",
        )
        start_at = st.number_input(
            "पहली फोटो किस दृश्य में लगे", 1, n, min(sel + 1, n), 1,
            key=f"bulkst_{P_['id']}_{ctr}",
        )
        extra_new = st.checkbox(
            "दृश्य कम पड़ें तो बची फोटो के लिए नए (बिना आवाज़ वाले) दृश्य जोड़ें",
            value=True, key=f"bulkx_{P_['id']}_{ctr}",
        )
        if files:
            st.caption(f"✅ {len(files)} फोटो चुनी गईं")
        if st.button("✅ सब फोटो लगाएँ", key=f"bulkgo_{P_['id']}_{ctr}",
                     type="primary", disabled=not files):
            saved, bad = [], []
            with st.spinner("फोटो लग रही हैं..."):
                for k, up in enumerate(files):
                    try:
                        pth = os.path.join(
                            P_["folder"], f"bulk_{int(time.time() * 1000) % 10**9}_{k}.jpg"
                        )
                        with Image.open(up) as img:
                            rgb = to_rgb(img)
                        rgb.thumbnail((2400, 2400))
                        rgb.save(pth, "JPEG", quality=92)
                        saved.append((pth, up.name))
                    except Exception:
                        bad.append(up.name)
                idx = int(start_at) - 1
                done = 0
                for pth, nm in saved:
                    add_to_library(pth, "आपकी फोटो: " + nm[:24])
                    if idx < len(scenes):
                        apply_image(idx, pth, "आपकी तस्वीर", "", flash=False)
                        idx += 1
                        done += 1
                    elif extra_new:
                        try:
                            scenes.append(new_scene(P_, "", pth, source="आपकी तस्वीर"))
                            idx += 1
                            done += 1
                        except Exception:
                            pass
                save_project(P_)
            msg = f"✅ {done} फोटो लग गईं"
            if bad:
                msg += f" ({len(bad)} फ़ाइलें तस्वीर के रूप में नहीं खुलीं: {', '.join(bad[:3])})"
            st.session_state["flash"] = msg
            st.session_state[ctr_key] = ctr + 1
            st.rerun()

    lib = boost_library()
    if not lib:
        st.caption("🗂️ अपने विषय-फोल्डर की फोटो जोड़ने का तरीका: ⚙️ सेटिंग में 'Ravi Boost' देखें।")
        return
    with st.expander("🗂️ मेरे फोल्डर की फोटो से लगाएँ"):
        names = list(lib.keys())
        fname = st.selectbox("फोल्डर (विषय)", names, key=f"libf_{sel}")
        files_ = lib[fname]["files"]
        per = 12
        pages = max(1, math.ceil(len(files_) / per))
        pg = 1
        if pages > 1:
            pg = int(st.number_input("पन्ना", 1, pages, 1, 1, key=f"libpg_{sel}"))
        chunk = files_[(pg - 1) * per: pg * per]
        st.caption(f"{len(files_)} फोटो • पन्ना {pg}/{pages}")
        for k in range(0, len(chunk), 3):
            cols = st.columns(3)
            for j in range(3):
                i_ = k + j
                if i_ >= len(chunk):
                    break
                pth_ = chunk[i_]
                with cols[j]:
                    th = _lib_thumb(pth_)
                    if th:
                        st.image(th, use_container_width=True)
                    if st.button("✅ लगाएँ", key=f"libpick_{sel}_{fname}_{pg}_{i_}",
                                 use_container_width=True):
                        try:
                            tmpf = os.path.join(
                                P_["folder"], f"libsel_{int(time.time() * 1000) % 10**9}.jpg"
                            )
                            with Image.open(pth_) as im_:
                                rgb_ = to_rgb(im_)
                            rgb_.thumbnail((2400, 2400))
                            rgb_.save(tmpf, "JPEG", quality=92)
                            apply_image(sel, tmpf, "मेरी लाइब्रेरी", fname)
                            st.rerun()
                        except Exception as e:
                            st.error(f"फोटो नहीं लग सकी: {str(e)[:120]}")


def boost_settings_ui():
    st.markdown("**🚀 Ravi Boost (फोटो और कैप्शन सुधार)**")
    S["use_gemini"] = st.checkbox(
        "🧠 Gemini इस्तेमाल करें (बार-बार 'व्यस्त' दिखे तो बंद कर दें; "
        "तब बिना Gemini के फोटो खोजी जाएँगी)",
        value=S.get("use_gemini", True), key="s_usegem",
    )
    S["karaoke"] = st.checkbox(
        "🌟 कैप्शन में बोले जा रहे शब्द पीले होते जाएँ",
        value=S.get("karaoke", True), key="s_karaoke",
    )
    S["lenient"] = st.checkbox(
        "🔎 नरम खोज: मेल खाती फोटो न मिले तो कम सटीक (मिलती-जुलती) फोटो भी लगाएँ "
        "(बंद रखें तो बेमेल फोटो नहीं लगेगी; नाम-आधारित विषय-खोज तब भी चलती है)",
        value=S.get("lenient", False), key="s_lenient",
    )
    if eff_pixabay():
        st.caption("✅ Pixabay key मिल गई (अच्छी और साफ़ फोटो इसी से मिलती हैं)।")
    else:
        st.warning(
            "⚠️ Pixabay key नहीं मिली, इसलिए अच्छी फोटो कम मिलेंगी। Streamlit में "
            "Manage app → Settings → Secrets में यह लाइन जोड़ें: "
            'PIXABAY_API_KEY = "आपकी key"'
        )
    lib = boost_library()
    if lib:
        st.success("📚 आपकी फोटो-लाइब्रेरी: " + ", ".join(f"{k} ({len(v['files'])})" for k, v in lib.items()))
    else:
        st.info(
            "📚 अपनी फोटो-लाइब्रेरी अभी खाली है। GitHub में इस ऐप की रिपॉज़िटरी में "
            "photos/<विषय>/ फ़ोल्डर बनाकर फोटो डालें (जैसे photos/shah-rukh-khan/)। "
            "फ़ोल्डर में keywords.txt फ़ाइल भी रख सकते हैं, जिसमें उस विषय के दूसरे नाम "
            "(हिंदी भी) कॉमा से अलग लिखें। कहानी में वह नाम आते ही ऐप उसी फ़ोल्डर की फोटो लगाएगा।"
        )
    with st.expander("🔧 पैच की स्थिति"):
        for line in BOOST_REPORT:
            st.caption(line)
'''


REPLACEMENTS = [
    (
        "एडिटर में 'एक साथ कई फोटो' और 'मेरे फोल्डर' वाले हिस्से",
        r'^([ \t]*)st\.markdown\("\*\*➕ नई फोटो लगाएँ\*\*"\)[ \t]*$',
        lambda m: f'{m.group(1)}boost_photo_tools(P, sel, scenes)\n{m.group(0)}',
    ),
    (
        "Gemini चालू/बंद का स्विच",
        r'def eff_gemini\(\):\n    return S\["gemini_key"\]\.strip\(\) or secret\("GEMINI_API_KEY"\)',
        lambda m: ('def eff_gemini():\n    if not S.get("use_gemini", True):\n        return ""\n'
                   '    return S["gemini_key"].strip() or secret("GEMINI_API_KEY")'),
    ),
    (
        "दृश्य के हिंदी शब्द भी लाइब्रेरी/खोज में",
        r'anchor, modern, tl, gem, english or sc\["text"\], topic,',
        lambda m: 'anchor, modern, tl, gem, ((english or "") + " " + sc["text"]), topic,',
    ),
    (
        "सेटिंग में Ravi Boost वाला हिस्सा",
        r'^([ \t]*)st\.caption\(APP_VERSION\)',
        lambda m: f'{m.group(1)}boost_settings_ui()\n{m.group(0)}',
    ),
    (
        "संस्करण का नाम",
        r'APP_VERSION = "Ravi Studio • संस्करण 9"',
        lambda m: 'APP_VERSION = "Ravi Studio • संस्करण 9 + Boost"',
    ),
]


def prepare(app_path):
    base = os.path.dirname(os.path.abspath(app_path))
    with open(app_path, encoding="utf-8") as f:
        src = f.read()
    src = re.sub(re.escape(LOADER_BEGIN) + r".*?" + re.escape(LOADER_END) + r"[ \t]*\n?",
                 "", src, flags=re.S)
    report = []
    for label, pattern, fn in REPLACEMENTS:
        new, n = re.subn(pattern, fn, src, count=1, flags=re.M)
        if n:
            src = new
            report.append("✅ " + label)
        else:
            report.append("⚠️ नहीं लगा: " + label)
    idx = src.find(MARK)
    if idx < 0:
        return None
    block = "\nBOOST_REPORT = " + repr(report) + "\n" + OVERRIDE_CODE + "\n"
    src = src[:idx + 1] + block + src[idx + 1:]
    out = os.path.join(base, "ravi_patched_app.py")
    old = ""
    if os.path.exists(out):
        with open(out, encoding="utf-8") as f:
            old = f.read()
    if old != src:
        with open(out, "w", encoding="utf-8") as f:
            f.write(src)
    return out


def run(app_path):
    if not app_path:
        return
    try:
        target = prepare(app_path)
    except Exception:
        return
    if not target:
        return
    import streamlit as st
    runpy.run_path(target, run_name="__main__")
    st.stop()
