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
st.write("अपनी स्क्रिप्ट डालें। ऐप सीन बनाएगा और संबंधित तस्वीरें खोजने में मदद करेगा।")

script = st.text_area(
    "📝 अपनी पूरी हिंदी स्क्रिप्ट यहाँ डालें",
    height=250
)

video_format = st.selectbox(
    "वीडियो का आकार",
    ["YouTube Shorts (9:16)", "Landscape (16:9)", "Square (1:1)"]
)

def split_scenes(text):
    parts = re.split(r'(?<=[।.!?])\s+|\n+', text.strip())
    return [p.strip() for p in parts if len(p.strip()) > 10]

def search_image(query):
    headers = {
        "User-Agent": "RaviAIImageApp/1.0"
    }

    try:
        # हिंदी सीन को अंग्रेज़ी में बदलने की कोशिश
        translate_url = "https://translate.googleapis.com/translate_a/single"

        translated = requests.get(
            translate_url,
            params={
                "client": "gtx",
                "sl": "hi",
                "tl": "en",
                "dt": "t",
                "q": query
            },
            headers=headers,
            timeout=15
        )

        translated.raise_for_status()
        translated_text = " ".join(
            item[0]
            for item in translated.json()[0]
            if item[0]
        )

        # Wikimedia Commons पर अंग्रेज़ी में तस्वीर खोजें
        url = "https://commons.wikimedia.org/w/api.php"

        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": translated_text,
            "gsrnamespace": 6,
            "gsrlimit": 10,
            "prop": "imageinfo",
            "iiprop": "url",
            "format": "json"
        }

        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=20
        )

        response.raise_for_status()

        pages = response.json().get(
            "query", {}
        ).get("pages", {})

        for page in pages.values():
            info = page.get("imageinfo", [])

            if info:
                image_url = info[0].get("url")

                if image_url:
                    return image_url

    except Exception:
        return None

    return None
