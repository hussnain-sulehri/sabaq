"""
Sabaq - Phase 2
Upload a lecture recording, get a transcript, then a corrected transcript
where transliterated technical terms are restored to English.

Run:
    streamlit run app.py
"""

import os
import tempfile

import assemblyai as aai
import streamlit as st

from config import get_key, mask
from normalizer import clean_transcript

MAX_MB = 25
ALLOWED = ["mp3", "wav", "m4a", "mp4"]

st.set_page_config(page_title="Sabaq", page_icon="🎓", layout="wide")
st.title("Sabaq")
st.caption("Lecture notes for classrooms that mix Urdu and English")

api_key = get_key("ASSEMBLYAI_API_KEY")

if not api_key:
    st.error(
        "No AssemblyAI key found.\n\n"
        "Local: create a .env file with ASSEMBLYAI_API_KEY=your_key\n\n"
        "Deployed: add ASSEMBLYAI_API_KEY in the app's Secrets settings"
    )
    st.stop()

aai.settings.api_key = api_key
st.sidebar.caption(f"Key loaded: {mask(api_key)}")

use_fuzzy = st.sidebar.checkbox(
    "Fuzzy matching for unseen spellings", value=False,
    help="Catches spellings the glossary has not seen. Turn off to compare.",
)

uploaded = st.file_uploader(f"Upload a lecture recording (max {MAX_MB} MB)", type=ALLOWED)

if uploaded:
    size_mb = uploaded.size / (1024 * 1024)

    if size_mb > MAX_MB:
        st.error(f"That file is {size_mb:.1f} MB. Keep it under {MAX_MB} MB.")
        st.stop()

    st.audio(uploaded)

    if st.button("Transcribe", type="primary"):
        audio_path = None
        try:
            suffix = os.path.splitext(uploaded.name)[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(uploaded.getbuffer())
                audio_path = tmp.name

            config = aai.TranscriptionConfig(language_detection=True)

            with st.spinner("Transcribing. Roughly a third of the audio length."):
                transcript = aai.Transcriber().transcribe(audio_path, config=config)

            if transcript.status == aai.TranscriptStatus.error:
                st.error("Transcription failed. Check the audio file and try again.")
                st.stop()

            raw = transcript.text or ""

            if not raw:
                st.warning("No speech detected in that file.")
                st.stop()

            corrected, changes = clean_transcript(raw, use_fuzzy=use_fuzzy)

            st.session_state["raw"] = raw
            st.session_state["corrected"] = corrected
            st.session_state["changes"] = changes
            st.session_state["duration"] = transcript.audio_duration

        except Exception:
            # Never surface the raw exception, some SDKs include the key in it.
            st.error("Something went wrong during transcription. Try again.")

        finally:
            if audio_path and os.path.exists(audio_path):
                os.remove(audio_path)


if "corrected" in st.session_state:
    raw = st.session_state["raw"]
    corrected = st.session_state["corrected"]
    changes = st.session_state["changes"]
    total_fixed = sum(c["times"] for c in changes)

    c1, c2, c3 = st.columns(3)
    c1.metric("Words", len(raw.split()))
    c2.metric("Terms corrected", total_fixed)
    if st.session_state.get("duration"):
        c3.metric("Length", f"{st.session_state['duration']} sec")

    left, right = st.columns(2)

    with left:
        st.subheader("Raw transcript")
        st.caption("Straight from the speech API")
        st.write(raw)

    with right:
        st.subheader("Corrected transcript")
        st.caption("Technical terms restored to English")
        st.write(corrected)

    if changes:
        st.subheader("What was corrected")
        st.dataframe(changes, use_container_width=True, hide_index=True)
    else:
        st.info("No known technical terms found in this transcript.")

    d1, d2 = st.columns(2)
    d1.download_button(
        "Download raw transcript", data=raw,
        file_name="transcript_raw.txt", mime="text/plain",
    )
    d2.download_button(
        "Download corrected transcript", data=corrected,
        file_name="transcript_corrected.txt", mime="text/plain",
    )