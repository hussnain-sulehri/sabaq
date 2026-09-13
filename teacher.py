"""
Teaching layer for Sabaq.

Takes the corrected transcript and produces study material:

- Summary
- Key points
- Practice questions

The transcript is a code-switched Urdu + English computer science lecture.
Prompts explicitly tell Gemini that English technical terms are expected.

Gemini model handling:
- No model name is stored in .env or Streamlit Secrets.
- The app automatically checks available Gemini models.
- The first working model from the priority list is selected.
- This avoids deployment failures caused by model retirement,
  quota differences, or account-specific availability.

Normal workflow:

Transcript
    |
    |
Gemini selected model
    |
    |
Summary + Key points + Practice Questions
"""

import json
import random
import re
import time

from google import genai


# --------------------------------------------------
# Gemini model fallback system
# --------------------------------------------------

# Models are checked in this order.
# First working model will be used.

PREFERRED_MODELS = [
    "gemini-2.5-flash",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.5-flash",
]


# Cache selected model per API key
# Prevents testing models on every request
_MODEL_CACHE: dict[str, str] = {}

# Cache Gemini clients
_CLIENTS: dict[str, genai.Client] = {}

def _client(api_key: str) -> genai.Client:
    """
    Keep one Gemini client per API key.
    Creating new clients repeatedly caused connection issues
    during earlier testing.
    """

    if api_key not in _CLIENTS:
        _CLIENTS[api_key] = genai.Client(
            api_key=api_key
        )

    return _CLIENTS[api_key]

def get_available_model(api_key: str) -> str:
    """
    Automatically select a working Gemini model.

    The selected model is cached so future requests reuse it.

    Checks:
    - Model exists
    - API key has access
    - Request succeeds
    """

    if api_key in _MODEL_CACHE:
        return _MODEL_CACHE[api_key]

    client = _client(api_key)

    for model in PREFERRED_MODELS:

        try:
            # Small validation request
            client.models.generate_content(
                model=model,
                contents="Reply only OK"
            )

            _MODEL_CACHE[api_key] = model

            return model

        except Exception as error:

            print(f"Model unavailable: {model} -> {error}")

            continue

    raise RuntimeError("No Gemini model is available for this API key.")

# --------------------------------------------------
# Retry configuration
# --------------------------------------------------

MAX_ATTEMPTS = 3


# Errors where retrying will not help
PERMANENT = (
    "NOT_FOUND",
    "PERMISSION_DENIED",
    "INVALID_ARGUMENT",
    "UNAUTHENTICATED",
    "API key",
)

# --------------------------------------------------
# Technical term style settings
# --------------------------------------------------

TERM_STYLES = {

    "Keep terms in English": (
        "Keep every technical term in English, in Latin script, "
        "exactly as a computer science textbook writes it. "
        "Write everything else in the target language."
    ),

    "Translate terms to Urdu": (
        "Translate technical terms into Urdu as well. "
        "Use standard Urdu academic terminology where it exists. "
        "If no standard term exists, explain clearly in Urdu."
    ),

    "Urdu term, English in brackets": (
        "Write technical terms in Urdu followed by English "
        "inside brackets the first time they appear. "
        "After that use the Urdu term."
    ),
}

DEFAULT_STYLE = "Keep terms in English"

# --------------------------------------------------
# Gemini request handler
# --------------------------------------------------

def _is_permanent(error: Exception) -> bool:
    return any(
        marker in str(error)
        for marker in PERMANENT
    )

def _ask(api_key: str, prompt: str) -> str:
    """
    Send prompt to Gemini.
    Automatically:
    - selects available model
    - retries temporary failures
    - avoids retrying invalid keys/models
    """
    last_error = None

    for attempt in range(MAX_ATTEMPTS):

        try:

            client = _client(api_key)
            model = get_available_model(api_key)

            response = client.models.generate_content(model=model,contents=prompt)
            return (response.text or "").strip()

        except Exception as error:
            last_error = error
            if _is_permanent(error):
                raise

            # Remove cached client if broken
            _CLIENTS.pop(api_key,None)

            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(
                    (5 * (attempt + 1))
                    + random.uniform(0, 1)
                )


    raise last_error if last_error else RuntimeError("Gemini request failed")

# --------------------------------------------------
# Helpers
# --------------------------------------------------

def _extract_json(text: str):

    cleaned = re.sub(
        r"^```(?:json)?",
        "",
        text.strip()
    )
    cleaned = re.sub(
        r"```$",
        "",
        cleaned.strip()
    )

    try:
        return json.loads(cleaned)

    except json.JSONDecodeError:
        pass


    for pattern in (
        r"\{.*\}",
        r"\[.*\]"
    ):

        match = re.search(pattern,cleaned,re.DOTALL)

        if match:

            try:
                return json.loads(
                    match.group(0)
                )

            except json.JSONDecodeError:
                continue

    return None

def _rules(language, term_style):

    style = TERM_STYLES.get(term_style,TERM_STYLES[DEFAULT_STYLE])

    return (
        f"Write in {language}. "
        f"{style}"
    )

def _context(language, term_style):

    return f"""
The transcript below is from a computer science lecture.

The teacher uses Urdu mixed with English technical terms.
This is normal.

{_rules(language, term_style)}

Use short sentences.
Do not add information that is not present in the transcript.
"""

# --------------------------------------------------
# Main combined Gemini call
# --------------------------------------------------

def study_notes(
    api_key,
    transcript,
    language="English",
    term_style=DEFAULT_STYLE,
    question_count=5,
):

    prompt = f"""
You are helping a university student revise a lecture.

{_context(language, term_style)}

Create:

1. Summary
2. Key points
3. {question_count} practice questions with answers


Every question must be answerable only from the transcript.

Return JSON only:

{{
"summary":"",
"points":[],
"questions":[
{{
"question":"",
"answer":""
}}
]
}}


TRANSCRIPT:

{transcript}
"""
    data = _extract_json(
        _ask(
            api_key,
            prompt
        )
    )


    if not isinstance(
        data,
        dict
    ):
        return {}


    return {

        "summary":
            str(
                data.get(
                    "summary",
                    ""
                )
            ).strip(),


        "points":
            [
                str(x).strip()
                for x in data.get(
                    "points",
                    []
                )
                if str(x).strip()
            ],


        "questions":
            [
                {
                    "question":
                        q.get(
                            "question",
                            ""
                        ),

                    "answer":
                        q.get(
                            "answer",
                            ""
                        )
                }

                for q in data.get(
                    "questions",
                    []
                )

                if isinstance(
                    q,
                    dict
                )
            ],
    }



# --------------------------------------------------
# Fallback functions
# --------------------------------------------------

def summarize(
    api_key,
    transcript,
    language="English",
    term_style=DEFAULT_STYLE,
):

    return _ask(
        api_key,
        f"""
{_context(language, term_style)}

Write a short lecture summary.

TRANSCRIPT:

{transcript}
"""
    )



def key_points(
    api_key,
    transcript,
    language="English",
    term_style=DEFAULT_STYLE,
):

    result = _ask(
        api_key,
        f"""
{_context(language, term_style)}

Write important key points.
One point per line.

TRANSCRIPT:

{transcript}
"""
    )

    return [
        x.strip("- ")
        for x in result.split("\n")
        if x.strip()
    ]



def practice_questions(
    api_key,
    transcript,
    count=5,
    language="English",
    term_style=DEFAULT_STYLE,
):

    data = _extract_json(
        _ask(
            api_key,
            f"""
{_context(language, term_style)}

Create {count} questions with answers.

Return JSON only.

[
{{
"question":"",
"answer":""
}}
]
TRANSCRIPT:
{transcript}
"""
        )
    )

    if not isinstance(data,list):
        return []

    return [
        item
        for item in data
        if isinstance(item,dict)
    ]