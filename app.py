"""
Sabaq - Voice Teaching Assistant

Upload a lecture recording and get:

1. A corrected transcript, with technical terms restored to English
2. A summary, key points, and practice questions
3. A comparison across lectures, to show where the glossary holds and fails

Gemini model selection is automatic. Models are discovered with a listing
call, which costs no generation quota, and the app moves to the next model
when one runs out for the day.

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
from runs import build_run, load_runs, save_run, summary_rows, term_matrix, to_csv
from teacher import (
    TERM_STYLES,
    get_available_model,
    key_points,
    practice_questions,
    study_notes,
    summarize,
)

MAX_MB = 25
ALLOWED = ["mp3", "wav", "m4a", "mp4"]
NOTE_KEYS = ("summary", "points", "questions")
SUBJECTS = ["AI / ML", "Database", "Web development", "Other"]


# -----------------------------
# Page setup
# -----------------------------

st.set_page_config(page_title="Sabaq", page_icon="🎓", layout="wide")

st.title("Sabaq 🎓")
st.caption("AI lecture assistant for classrooms that mix Urdu and English")


# -----------------------------
# API keys
# -----------------------------

aai_key = get_key("ASSEMBLYAI_API_KEY")
gemini_key = get_key("GEMINI_API_KEY")

if not aai_key:
    st.error(
        "No AssemblyAI API key found.\n\n"
        "Local: add ASSEMBLYAI_API_KEY to your .env file\n\n"
        "Deployed: add it in the app's Secrets settings"
    )
    st.stop()

aai.settings.api_key = aai_key


# -----------------------------
# Sidebar
# -----------------------------

st.sidebar.caption(f"Speech key: {mask(aai_key)}")
st.sidebar.caption(f"Gemini key: {mask(gemini_key) if gemini_key else 'not set'}")

if gemini_key:
    try:
        st.sidebar.success(f"Model: {get_available_model(gemini_key)}")
    except Exception:
        st.sidebar.warning("No Gemini model available")

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


# -----------------------------
# Transcription
# -----------------------------

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


# -----------------------------
# Note generation
# -----------------------------

def generate_notes(transcript: str) -> None:
    """
    One combined request, falling back to separate calls if it will not parse.

    The free tier allows 20 generation requests per day per model, so the
    combined call is the normal path and costs one request instead of three.
    """
    with st.spinner("Generating study notes..."):
        notes = study_notes(gemini_key, transcript, notes_language, term_style)

    if notes.get("summary"):
        st.session_state["summary"] = notes["summary"]
        st.session_state["points"] = notes["points"]
        st.session_state["questions"] = notes["questions"]
        return

    st.info("Combined reply could not be read. Falling back to separate calls.")

    steps = [
        ("summary", "Creating summary",
         lambda: summarize(gemini_key, transcript, notes_language, term_style)),
        ("points", "Creating key points",
         lambda: key_points(gemini_key, transcript, notes_language, term_style)),
        ("questions", "Creating questions",
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


# -----------------------------
# Upload
# -----------------------------

uploaded = st.file_uploader(
    f"Upload a lecture recording (max {MAX_MB} MB)", type=ALLOWED
)

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

            st.success("Transcript ready")

        except Exception:
            # Never surface the raw exception, some SDKs include the key in it.
            st.error("Transcription failed. Try again.")


# -----------------------------
# Results
# -----------------------------

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

    tab_notes, tab_transcript, tab_changes, tab_compare = st.tabs(
        ["Study notes", "Transcript", "Corrections", "Compare lectures"]
    )

    # --- Transcript ---
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

    # --- Corrections ---
    with tab_changes:
        if changes:
            st.caption("Every spelling the normalizer replaced, and how often.")
            st.dataframe(changes, use_container_width=True, hide_index=True)
        else:
            st.info("No known technical terms found in this transcript.")

        st.divider()
        st.caption("Save this run to compare it against other lectures.")

        name_col, subject_col, save_col = st.columns([2, 2, 1])
        label = name_col.text_input("Lecture name", placeholder="Overfitting")
        subject = subject_col.selectbox("Subject", SUBJECTS)

        if save_col.button("Save run", disabled=not label):
            run = build_run(
                label, subject, raw, corrected, changes,
                st.session_state.get("duration"),
            )
            st.session_state.setdefault("session_runs", []).append(run)

            if save_run(run):
                st.success(f"Saved to runs/ as {label}")
            else:
                st.success(f"Saved for this session as {label}")
                st.caption("Disk is read only here, so this run lasts until reload.")

    # --- Comparison ---
    with tab_compare:
        saved = load_runs()
        seen = {r["label"] for r in saved}
        saved += [
            r for r in st.session_state.get("session_runs", [])
            if r["label"] not in seen
        ]

        if len(saved) < 2:
            st.info(
                "Save at least two runs to compare them. Transcribe another "
                "lecture, then save it from the corrections tab."
            )
        else:
            st.subheader("Per lecture")
            st.dataframe(summary_rows(saved), use_container_width=True,
                         hide_index=True)
            st.caption(
                "Corrections per 100 words shows how term-heavy a lecture is. "
                "Unique terms shows how much of the glossary it exercised."
            )

            st.subheader("Which terms appeared where")
            st.caption(
                "Blank cells on a new subject mean the glossary does not cover "
                "that vocabulary yet."
            )
            st.dataframe(term_matrix(saved), use_container_width=True,
                         hide_index=True)

            st.download_button(
                "Download comparison as CSV",
                data=to_csv(saved),
                file_name="sabaq_lecture_comparison.csv",
                mime="text/csv",
            )

    # --- Study notes ---
    with tab_notes:
        if not gemini_key:
            st.warning("Add GEMINI_API_KEY to generate study notes.")
        else:
            if st.button("Generate study notes"):
                try:
                    generate_notes(corrected)
                except Exception:
                    st.error(
                        "Could not generate notes. Every model may be out of "
                        "daily quota. Try again after the reset."
                    )

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