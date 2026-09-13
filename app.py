"""
Sabaq - Voice Teaching Assistant

Upload a lecture recording and get:

1. Corrected transcript
2. Technical term restoration
3. AI generated summary
4. Key points
5. Practice questions

Gemini model selection is automatic.
The app checks available Gemini models
for the configured API key and uses the
first working model.

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
    get_available_model,
    TERM_STYLES,
    key_points,
    practice_questions,
    study_notes,
    summarize,
)



MAX_MB = 25

ALLOWED = [
    "mp3",
    "wav",
    "m4a",
    "mp4",
]


NOTE_KEYS = (
    "summary",
    "points",
    "questions",
)



# -----------------------------
# Page Setup
# -----------------------------


st.set_page_config(
    page_title="Sabaq",
    page_icon="🎓",
    layout="wide",
)


st.title("Sabaq 🎓")

st.caption(
    "AI lecture assistant for Urdu + English mixed classrooms"
)



# -----------------------------
# API Keys
# -----------------------------


aai_key = get_key(
    "ASSEMBLYAI_API_KEY"
)


gemini_key = get_key(
    "GEMINI_API_KEY"
)



if not aai_key:

    st.error(
        """
No AssemblyAI API key found.

Add:

ASSEMBLYAI_API_KEY

to:

.env

or

Streamlit Secrets
"""
    )

    st.stop()



aai.settings.api_key = aai_key



# -----------------------------
# Sidebar
# -----------------------------


st.sidebar.caption(
    f"Speech key: {mask(aai_key)}"
)


st.sidebar.caption(
    f"Gemini key: {mask(gemini_key) if gemini_key else 'not set'}"
)



# Automatic Gemini model detection

if gemini_key:

    try:

        selected_model = get_available_model(
            gemini_key
        )


        st.sidebar.success(
            f"Gemini model: {selected_model}"
        )


    except Exception:

        selected_model = None


        st.sidebar.warning(
            "Gemini model unavailable"
        )



st.sidebar.divider()



use_fuzzy = st.sidebar.checkbox(
    "Fuzzy matching for unseen spellings",
    value=False,
    help=(
        "Disabled by default because short Urdu words "
        "can be incorrectly matched."
    ),
)



notes_language = st.sidebar.radio(
    "Notes language",
    [
        "English",
        "Urdu"
    ]
)



term_style = st.sidebar.selectbox(
    "Technical terms",
    list(TERM_STYLES.keys()),
)



# -----------------------------
# Audio Upload
# -----------------------------


uploaded = st.file_uploader(
    f"Upload lecture recording (max {MAX_MB} MB)",
    type=ALLOWED
)



# -----------------------------
# Transcription Function
# -----------------------------


def transcribe(file):

    """
    Upload audio to AssemblyAI
    and return transcript.
    """


    audio_path = None


    try:

        suffix = os.path.splitext(
            file.name
        )[1]


        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix
        ) as tmp:


            tmp.write(
                file.getbuffer()
            )


            audio_path = tmp.name



        config = aai.TranscriptionConfig(
            language_detection=True
        )



        result = aai.Transcriber().transcribe(
            audio_path,
            config=config
        )



        if result.status == aai.TranscriptStatus.error:

            return "", None



        return (
            result.text or "",
            result.audio_duration
        )



    finally:


        if audio_path and os.path.exists(audio_path):

            os.remove(audio_path)





# -----------------------------
# Generate AI Notes
# -----------------------------


def generate_notes(transcript):


    """
    One Gemini request generates:

    - summary
    - key points
    - questions

    Falls back to separate calls
    if JSON parsing fails.
    """


    with st.spinner(
        "Generating study notes..."
    ):


        notes = study_notes(
            gemini_key,
            transcript,
            notes_language,
            term_style
        )



    if notes.get("summary"):


        st.session_state["summary"] = notes["summary"]

        st.session_state["points"] = notes["points"]

        st.session_state["questions"] = notes["questions"]


        return



    st.info(
        "Using fallback generation."
    )



    steps = [

        (
            "summary",
            "Creating summary",
            lambda:
            summarize(
                gemini_key,
                transcript,
                notes_language,
                term_style
            )
        ),


        (
            "points",
            "Creating key points",
            lambda:
            key_points(
                gemini_key,
                transcript,
                notes_language,
                term_style
            )
        ),


        (
            "questions",
            "Creating questions",
            lambda:
            practice_questions(
                gemini_key,
                transcript,
                5,
                notes_language,
                term_style
            )
        )

    ]



    for index, (key, label, func) in enumerate(steps):

        try:

            with st.spinner(label):

                st.session_state[key] = func()



        except Exception:

            st.warning(
                f"{label} failed."
            )



        if index < len(steps)-1:

            time.sleep(4)





# -----------------------------
# Upload Processing
# -----------------------------


if uploaded:


    size_mb = uploaded.size / (
        1024 * 1024
    )



    if size_mb > MAX_MB:

        st.error(
            f"File size {size_mb:.1f}MB. "
            f"Maximum allowed {MAX_MB}MB."
        )

        st.stop()



    st.audio(uploaded)



    if st.button(
        "Transcribe",
        type="primary"
    ):


        try:


            with st.spinner(
                "Transcribing lecture..."
            ):


                raw, duration = transcribe(
                    uploaded
                )



            if not raw:

                st.warning(
                    "No speech detected."
                )

                st.stop()



            corrected, changes = clean_transcript(
                raw,
                use_fuzzy=use_fuzzy
            )



            st.session_state["raw"] = raw

            st.session_state["corrected"] = corrected

            st.session_state["changes"] = changes

            st.session_state["duration"] = duration



            for item in NOTE_KEYS:

                st.session_state.pop(
                    item,
                    None
                )



            st.success(
                "Transcript ready"
            )



        except Exception:


            st.error(
                "Transcription failed."
            )





# -----------------------------
# Display Results
# -----------------------------


if "corrected" in st.session_state:


    raw = st.session_state["raw"]

    corrected = st.session_state["corrected"]

    changes = st.session_state["changes"]



    total_fixed = sum(
        c["times"]
        for c in changes
    )



    c1, c2, c3 = st.columns(3)



    c1.metric(
        "Words",
        len(raw.split())
    )


    c2.metric(
        "Terms corrected",
        total_fixed
    )


    if st.session_state.get("duration"):

        c3.metric(
            "Length",
            f"{st.session_state['duration']} sec"
        )



    tab_notes, tab_transcript, tab_changes = st.tabs(
        [
            "Study Notes",
            "Transcript",
            "Corrections"
        ]
    )



    with tab_transcript:


        left, right = st.columns(2)



        with left:

            st.subheader(
                "Raw Transcript"
            )

            st.write(raw)



        with right:

            st.subheader(
                "Corrected Transcript"
            )

            st.write(corrected)



    with tab_changes:


        if changes:

            st.dataframe(
                changes,
                use_container_width=True
            )


        else:

            st.info(
                "No corrections."
            )



    with tab_notes:


        if not gemini_key:


            st.warning(
                "Add Gemini API key."
            )



        elif st.button(
            "Generate Study Notes"
        ):


            try:

                generate_notes(
                    corrected
                )


            except Exception:

                st.error(
                    "Gemini generation failed."
                )

        if st.session_state.get("summary"):


            st.subheader("Summary")

            st.write(st.session_state["summary"])

        if st.session_state.get("points"):
            st.subheader("Key Points")

            for p in st.session_state["points"]:
                st.markdown(
                    f"- {p}"
                )
        if st.session_state.get("questions"):

            st.subheader("Practice Questions")

            for i, q in enumerate(
                st.session_state["questions"],
                start=1
            ):

                with st.expander(
                    f"{i}. {q['question']}"
                ):

                    st.write(
                        q["answer"]
                    )