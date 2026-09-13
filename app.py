"""
Sabaq - voice teaching assistant for classrooms that mix Urdu and English.

Upload a lecture recording. Get a corrected transcript, a summary,
key points, and practice questions.

Run:
    streamlit run app.py
"""

import os
import tempfile
import time
import assemblyai as aai
import streamlit as st
from config import get_key, mask
from normalizer import clean_transcript
from teacher import (
    MODEL,
    TERM_STYLES,
    key_points,
    practice_questions,
    study_notes,
    summarize,
)

MAX_MB = 25
ALLOWED = ["mp3", "wav", "m4a", "mp4"]
NOTE_KEYS = ("summary", "points", "questions")

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
st.sidebar.caption(f"Model: {MODEL}")
st.sidebar.divider()

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


def transcribe(file) -> tuple[str, int | None]:
    """Send an uploaded file to AssemblyAI. Returns (text, duration_seconds)."""
    audio_path = None
    try:
        suffix = os.path.splitext(file.name)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file.getbuffer())
            audio_path = tmp.name

        config = aai.TranscriptionConfig(language_detection=True)
        result = aai.Transcriber().transcribe(audio_path, config=config)

        if result.status == aai.TranscriptStatus.error:
            return "", None

        return result.text or "", result.audio_duration

    finally:
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)


def generate_notes(transcript: str) -> None:
    """
    One combined call, falling back to separate calls if it cannot be parsed.

    Gemini's free tier limits requests per minute, so the combined call is
    the normal path. The fallback spaces its three calls out to stay inside
    the same limit.
    """
    with st.spinner("Reading the lecture..."):
        notes = study_notes(gemini_key, transcript, notes_language, term_style)

    if notes.get("summary"):
        st.session_state["summary"] = notes["summary"]
        st.session_state["points"] = notes["points"]
        st.session_state["questions"] = notes["questions"]
        return

    st.info("Combined reply could not be read. Falling back to separate calls.")

    steps = [
        ("summary", "Writing summary",
         lambda: summarize(gemini_key, transcript, notes_language, term_style)),
        ("points", "Pulling key points",
         lambda: key_points(gemini_key, transcript, notes_language, term_style)),
        ("questions", "Writing questions",
         lambda: practice_questions(gemini_key, transcript, 5, notes_language, term_style)),
    ]

    for index, (state_key, label, call) in enumerate(steps):
        try:
            with st.spinner(label):
                st.session_state[state_key] = call()
        except Exception:
            st.warning(f"{label} failed. Press the button again to retry.")

        if index < len(steps) - 1:
            time.sleep(4)


uploaded = st.file_uploader(f"Upload a lecture recording (max {MAX_MB} MB)", type=ALLOWED)

if uploaded:
    size_mb = uploaded.size / (1024 * 1024)

    if size_mb > MAX_MB:
        st.error(f"That file is {size_mb:.1f} MB. Keep it under {MAX_MB} MB.")
        st.stop()

    st.audio(uploaded)

    if st.button("Transcribe", type="primary"):
        try:
            with st.spinner("Transcribing. Roughly a third of the audio length."):
                raw, duration = transcribe(uploaded)

            if not raw:
                st.warning("No speech detected, or transcription failed.")
                st.stop()

            corrected, changes = clean_transcript(raw, use_fuzzy=use_fuzzy)

            st.session_state["raw"] = raw
            st.session_state["corrected"] = corrected
            st.session_state["changes"] = changes
            st.session_state["duration"] = duration

            # Clear old notes so they never belong to a previous recording.
            for stale in NOTE_KEYS:
                st.session_state.pop(stale, None)

        except Exception:
            # Never surface the raw exception, some SDKs include the key in it.
            st.error("Something went wrong during transcription. Try again.")


if "corrected" in st.session_state:
    raw = st.session_state["raw"]
    corrected = st.session_state["corrected"]
    changes = st.session_state["changes"]
    total_fixed = sum(c["times"] for c in changes)

    col1, col2, col3 = st.columns(3)
    col1.metric("Words", len(raw.split()))
    col2.metric("Terms corrected", total_fixed)
    if st.session_state.get("duration"):
        col3.metric("Length", f"{st.session_state['duration']} sec")

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

        down1, down2 = st.columns(2)
        down1.download_button(
            "Download raw", data=raw,
            file_name="transcript_raw.txt", mime="text/plain",
        )
        down2.download_button(
            "Download corrected", data=corrected,
            file_name="transcript_corrected.txt", mime="text/plain",
        )

    with tab_changes:
        if changes:
            st.caption("Every spelling the normalizer replaced, and how often.")
            st.dataframe(changes, use_container_width=True, hide_index=True)
        else:
            st.info("No known technical terms found in this transcript.")

    with tab_notes:
        if not gemini_key:
            st.warning("Add GEMINI_API_KEY to generate study notes.")
        else:
            if st.button("Generate study notes"):
                try:
                    generate_notes(corrected)
                except Exception:
                    st.error("Could not generate notes. Try again in a moment.")

            if st.session_state.get("summary"):
                st.subheader("Summary")
                st.write(st.session_state["summary"])

            if st.session_state.get("points"):
                st.subheader("Key points")
                for point in st.session_state["points"]:
                    st.markdown(f"- {point}")

            if st.session_state.get("questions"):
                st.subheader("Practice questions")
                for number, item in enumerate(st.session_state["questions"], start=1):
                    with st.expander(f"{number}. {item['question']}"):
                        st.write(item["answer"])
