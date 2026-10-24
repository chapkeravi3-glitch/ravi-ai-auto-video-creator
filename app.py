import streamlit as st
import requests
import re
from io import BytesIO
from PIL import Image

st.set_page_config(
    page_title="Ravi AI Auto Video Creator",
    page_icon="🎬",
    layout="wide"
)

st.title("🎬 Ravi AI Auto Video Creator")
st.write("अपनी स्क्रिप्ट डालो। ऐप सीन बनाएगा और तस्वीरें खोजेगा।")

script = st.text_area(
    "📝 अपनी पूरी हिंदी स्क्रिप्ट यहाँ डालो",
    height=250
)

video_format = st.selectbox(
    "वीडियो का आकार चुनो",
    ["YouTube Shorts (9:16)", "Landscape (16:9)", "Square (1:1)"]
)

def split_scenes(text):
    parts = re.split(r'(?<=[।.!?])\s+|\n+', text.strip())
    return [p.strip() for p in parts if len(p.strip()) > 5]

def translate_to_english(text):
    try:
        response = requests.get(
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
        response.raise_for_status()
        data = response.json()
        return " ".join(item[0] for item in data[0] if item[0])
    except Exception:
        return text

def search_image(query):
    headers = {"User-Agent": "RaviAIImageApp/1.0"}

    english_query = translate_to_english(query)

    words = english_query.split()
    short_query = " ".join(words[:7])

    for search_text in [short_query, " ".join(words[:3])]:
        try:
            response = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": search_text,
                    "gsrnamespace": 6,
                    "gsrlimit": 10,
                    "prop": "imageinfo",
                    "iiprop": "url",
                    "iiurlwidth": 900,
                    "format": "json"
                },
                headers=headers,
                timeout=20
            )
            response.raise_for_status()

            pages = response.json().get("query", {}).get("pages", {})

            for page in pages.values():
                for info in page.get("imageinfo", []):
                    image_url = info.get("thumburl") or info.get("url")
                    if image_url:
                        return image_url

        except Exception:
            continue

    return None

if st.button("🔎 सीन बनाएँ और तस्वीरें खोजें"):
    if not script.strip():
        st.warning("पहले अपनी स्क्रिप्ट लिखो।")
    else:
        scenes = split_scenes(script)

        if not scenes:
            st.warning("स्क्रिप्ट थोड़ी और लिखो।")
        else:
            st.success(f"कुल {len(scenes)} सीन मिले।")

            for i, scene in enumerate(scenes, start=1):
                st.subheader(f"🎬 सीन {i}")
                st.write(scene)

                with st.spinner(f"सीन {i} की तस्वीर खोज रहे हैं..."):
                    image_url = search_image(scene)

                if image_url:
                    try:
                        response = requests.get(
                            image_url,
                            headers={"User-Agent": "RaviAIImageApp/1.0"},
                            timeout=20
                        )
                        response.raise_for_status()

                        image_bytes = response.content
                        image = Image.open(BytesIO(image_bytes))
                        st.image(image, caption=f"सीन {i}", use_container_width=True)

                        st.download_button(
                            f"⬇️ सीन {i} की तस्वीर डाउनलोड करो",
                            data=image_bytes,
                            file_name=f"scene_{i}.jpg",
                            mime="image/jpeg",
                            key=f"download_{i}"
                        )

                    except Exception:
                        st.warning("तस्वीर मिली, लेकिन उसे खोला नहीं जा सका।")
                else:
                    st.warning("इस सीन के लिए तस्वीर नहीं मिली।")

            st.info(
                "यह अभी तस्वीरें खोजने वाला शुरुआती संस्करण है। "
                "AI इमेज जनरेशन, हिंदी आवाज़, एनिमेशन और MP4 वीडियो "
                "अगले चरणों में जोड़े जाएँगे।"
            )
