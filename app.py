import streamlit as st

st.set_page_config(
    page_title="Ravi AI Auto Video Creator",
    page_icon="🎬",
    layout="centered"
)

st.title("🎬 Ravi AI Auto Video Creator")
st.write("अपनी स्क्रिप्ट डालिए। ऐप उसे सीन में बाँटने में मदद करेगा।")

script = st.text_area(
    "अपनी वीडियो स्क्रिप्ट यहाँ डालें",
    height=250,
    placeholder="यहाँ अपनी हिंदी स्क्रिप्ट लिखें..."
)

video_format = st.selectbox(
    "वीडियो का आकार चुनें",
    ["YouTube Shorts (9:16)", "Landscape (16:9)", "Square (1:1)"]
)

if st.button("📝 स्क्रिप्ट से सीन बनाएँ"):
    if not script.strip():
        st.warning("पहले अपनी स्क्रिप्ट डालें।")
    else:
        paragraphs = [
            p.strip()
            for p in script.splitlines()
            if p.strip()
        ]

        if len(paragraphs) == 1:
            sentences = script.replace("।", "।\n").splitlines()
            paragraphs = [s.strip() for s in sentences if s.strip()]

        st.success(f"स्क्रिप्ट तैयार! कुल {len(paragraphs)} सीन मिले।")

        for i, paragraph in enumerate(paragraphs, 1):
            st.subheader(f"सीन {i}")
            st.write(paragraph)

        st.info(
            "अगले चरण में AI तस्वीरें, हिंदी आवाज़, "
            "एनिमेशन और MP4 वीडियो बनाने की सुविधाएँ जोड़ेंगे।"
      )
