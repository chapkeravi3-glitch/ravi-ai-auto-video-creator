# voice_engine.py  — Ravi Studio के लिए इंसान जैसी आवाज़
# requirements.txt में जोड़ें: google-genai, edge-tts
import os, re, wave, asyncio
import streamlit as st
from google import genai
from google.genai import types

GEMINI_MODEL = "gemini-2.5-flash-preview-tts"  # नाम बदला हो तो AI Studio में नया TTS मॉडल देख लें
RATE = 24000  # Gemini TTS: 24kHz, 16-bit, mono

# आवाज़ें: महिला - Kore, Leda, Zephyr, Aoede | पुरुष - Puck, Charon, Fenrir, Orus
DEFAULT_VOICE = "Kore"

STYLE = ("नीचे का टेक्स्ट एक अच्छे हिंदी कहानीकार की तरह, स्वाभाविक, गर्मजोशी भरी "
         "इंसानी आवाज़ में, सही ठहराव और भाव के साथ पढ़ो:\n\n")


def _api_key():
    try:
        return st.secrets["GEMINI_API_KEY"]
    except Exception:
        return os.environ.get("GEMINI_API_KEY", "")


def _chunks(text, limit=700):
    sents = re.split(r"(?<=[।!?\.])\s+", text.strip())
    out, cur = [], ""
    for s in sents:
        if len(cur) + len(s) < limit:
            cur += (" " if cur else "") + s
        else:
            if cur:
                out.append(cur)
            cur = s
    if cur:
        out.append(cur)
    return out


def _gemini_pcm(client, text, voice):
    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=STYLE + text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                )
            ),
        ),
    )
    return resp.candidates[0].content.parts[0].inline_data.data


def _gemini_tts(text, out_wav, voice):
    client = genai.Client(api_key=_api_key())
    pcm = b""
    for ch in _chunks(text):
        last_err = None
        for _ in range(3):  # 3 बार कोशिश
            try:
                pcm += _gemini_pcm(client, ch, voice)
                last_err = None
                break
            except Exception as e:
                last_err = e
        if last_err:
            raise last_err
    with wave.open(out_wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return out_wav


def _edge_tts(text, out_mp3, voice="hi-IN-SwaraNeural"):
    import edge_tts
    async def go():
        await edge_tts.Communicate(text, voice, rate="-5%").save(out_mp3)
    asyncio.run(go())
    return out_mp3


def make_voice(text, out_path_no_ext, voice=DEFAULT_VOICE):
    """आवाज़ फ़ाइल बनाकर उसका पाथ लौटाता है। पहले Gemini, फेल हो तो edge_tts।"""
    try:
        return _gemini_tts(text, out_path_no_ext + ".wav", voice)
    except Exception as e:
        print("Gemini आवाज़ नहीं बनी, edge_tts पर जा रहे हैं:", e)
        return _edge_tts(text, out_path_no_ext + ".mp3")
