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
    url = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query + " filetype:bitmap",
        "gsrnamespace": 6,
        "gsrlimit": 5,
        "prop": "imageinfo",
        "iiprop": "url",
        "format": "json"
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": "RaviAIImageApp/1.0"},
            timeout=20
        )
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", {})
        for page in pages.values():
            info = page.get("imageinfo", [])
            if info:
                return info[0].get("url")
    except Exception:
        return None

    return None

if st.button("🔎 स्क्रिप्ट से सीन और तस्वीरें खोजें"):
    if not script.strip():
        st.warning("पहले अपनी स्क्रिप्ट डालें।")
    else:
        scenes = split_scenes(script)
        st.success(f"कुल {len(scenes)} सीन तैयार हुए।")

        for i, scene in enumerate(scenes, 1):
            st.subheader(f"सीन {i}")
            st.write(scene)

            with st.spinner("तस्वीर खोज रहे हैं..."):
                image_url = search_image(scene[:100])

            if image_url:
                try:
                    r = requests.get(image_url, timeout=20)
                    r.raise_for_status()
                    image = Image.open(BytesIO(r.content))
                    st.image(image, caption=f"सीन {i}", use_container_width=True)

                    st.download_button(
                        f"⬇️ सीन {i} की तस्वीर डाउनलोड करें",
                        data=r.content,
                        file_name=f"scene_{i}.jpg",
                        mime="image/jpeg",
                        key=f"download_{i}"
                    )
                except Exception:
                    st.info("तस्वीर मिली, लेकिन उसे खोला नहीं जा सका।")
            else:
                st.info("इस सीन के लिए तस्वीर नहीं मिली।")

        st.info(
            "यह शुरुआती संस्करण है। अगले चरण में AI इमेज जनरेशन, "
            "हिंदी आवाज़, एनिमेशन और MP4 वीडियो बनाने की सुविधाएँ जोड़ेंगे।"
        )
