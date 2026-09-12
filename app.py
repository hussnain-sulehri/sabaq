"""
Sabaq - Phase 3
Upload a lecture recording. Get a corrected transcript, a summary,
key points, and practice questions.

Run:
    streamlit run app.py
"""

import os
import tempfile

import assemblyai as aai
import streamlit as st

from config import get_key, mask
from normalizer import clean_transcript
from teacher import TERM_STYLES, key_points, practice_questions, summarize

MAX_MB = 25
ALLOWED = ["mp3", "wav", "m4a", "mp4"]

st.set_page_config(page_title="Sabaq", page_icon="🎓", layout="wide")
st.title("Sabaq")
st.caption("Lecture notes for classrooms that mix Urdu and English")

aai_key = get_key("ASSEMBLYAI_API_KEY")
gemini_key = get_key("GEMINI_API_KEY")

if not aai_key:
    st.error(
        "No AssemblyAI key found.\n\n"
        "Local: add ASSEMBLYAI_API_KEY to your .env file\n\n"
        "Deployed: add it in the app's Secrets settings"
    )
    st.stop()

aai.settings.api_key = aai_key

st.sidebar.caption(f"Speech key: {mask(aai_key)}")
st.sidebar.caption(f"Notes key: {mask(gemini_key) if gemini_key else 'not set'}")

use_fuzzy = st.sidebar.checkbox(
    "Fuzzy matching for unseen spellings",
    value=False,
    help="Off by default. It corrupts short Urdu words. Turn on to see that failure.",
)

notes_language = st.sidebar.radio("Notes language", ["English", "Urdu"])
term_style = st.sidebar.selectbox(
    "Technical terms",
    list(TERM_STYLES.keys()),
    help="How English technical terms should appear in the notes.",
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
            # Clear old notes so they never belong to a previous recording.
            for k in ("summary", "points", "questions"):
                st.session_state.pop(k, None)

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

    tab_notes, tab_transcript, tab_changes = st.tabs(
        ["Study notes", "Transcript", "What was corrected"]
    )

    with tab_transcript:
        left, right = st.columns(2)
        with left:
            st.subheader("Raw")
            st.caption("Straight from the speech API")
            st.write(raw)
        with right:
            st.subheader("Corrected")
            st.caption("Technical terms restored to English")
            st.write(corrected)

        d1, d2 = st.columns(2)
        d1.download_button(
            "Download raw", data=raw,
            file_name="transcript_raw.txt", mime="text/plain",
        )
        d2.download_button(
            "Download corrected", data=corrected,
            file_name="transcript_corrected.txt", mime="text/plain",
        )

    with tab_changes:
        if changes:
            st.dataframe(changes, use_container_width=True, hide_index=True)
        else:
            st.info("No known technical terms found in this transcript.")

    with tab_notes:
        if not gemini_key:
            st.warning("Add GEMINI_API_KEY to generate study notes.")
        elif st.button("Generate study notes"):
            try:
                with st.spinner("Reading the lecture..."):
                    st.session_state["summary"] = summarize(
                        gemini_key, corrected, notes_language, term_style
                    )
                    st.session_state["points"] = key_points(
                        gemini_key, corrected, notes_language, term_style
                    )
                    st.session_state["questions"] = practice_questions(
                        gemini_key, corrected, 5, notes_language, term_style
                    )
            except Exception:
                st.error("Could not generate notes. Check the key and try again.")

        if st.session_state.get("summary"):
            st.subheader("Summary")
            st.write(st.session_state["summary"])

        if st.session_state.get("points"):
            st.subheader("Key points")
            for point in st.session_state["points"]:
                st.markdown(f"- {point}")

        if st.session_state.get("questions"):
            st.subheader("Practice questions")
            for i, item in enumerate(st.session_state["questions"], start=1):
                with st.expander(f"{i}. {item['question']}"):
                    st.write(item["answer"])
