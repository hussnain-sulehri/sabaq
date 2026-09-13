"""
Teaching layer for Sabaq.

Takes the corrected transcript and produces study material:
a summary, key points, and practice questions.

The transcript is code-switched Urdu and English, so every prompt says so.
Without that instruction the model tends to answer only in English and
drops half the meaning.

Everything is requested in a single call. Three separate calls hit Gemini's
free-tier per-minute limit. Separate functions are kept as a fallback for
when the combined reply cannot be parsed.
"""

import json
import random
import re
import time

from google import genai

from config import get_setting

# Model names get retired. gemini-2.5-flash was withdrawn for new users
# during this build. Set GEMINI_MODEL in .env or Streamlit secrets to
# override without touching the code.
MODEL = get_setting("GEMINI_MODEL", "gemini-3.6-flash")

MAX_ATTEMPTS = 3

# Errors that will never succeed on a retry. Failing fast on these keeps a
# bad key or a dead model name from costing three attempts and six seconds.
PERMANENT = ("NOT_FOUND", "PERMISSION_DENIED", "INVALID_ARGUMENT",
             "UNAUTHENTICATED", "API key")

# How technical terms should appear in the generated notes.
TERM_STYLES = {
    "Keep terms in English": (
        "Keep every technical term in English, in Latin script, exactly as a "
        "textbook would write it, in lowercase unless it is a proper noun. "
        "Write everything else in the target language."
    ),
    "Translate terms to Urdu": (
        "Translate technical terms into Urdu as well. Do not leave any English "
        "word in Latin script. Use the standard Urdu academic term where one "
        "exists, and a clear Urdu description where it does not."
    ),
    "Urdu term, English in brackets": (
        "Write each technical term in Urdu, followed by the English term in "
        "brackets the first time it appears in the text. After the first "
        "mention, use the Urdu term alone."
    ),
}

DEFAULT_STYLE = "Keep terms in English"

_CLIENTS: dict[str, genai.Client] = {}


def _client(api_key: str) -> genai.Client:
    """Keep one client per key. A fresh client per call gets closed mid-request."""
    if api_key not in _CLIENTS:
        _CLIENTS[api_key] = genai.Client(api_key=api_key)
    return _CLIENTS[api_key]


def _is_permanent(error: Exception) -> bool:
    return any(marker in str(error) for marker in PERMANENT)


def _ask(api_key: str, prompt: str) -> str:
    """
    Call the model, retrying transient failures.

    Rate limits, timeouts and dropped connections are the usual cause of a
    demo failing live. Permanent errors are raised at once instead of being
    retried pointlessly.
    """
    last_error: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            client = _client(api_key)
            response = client.models.generate_content(model=MODEL, contents=prompt)
            return (response.text or "").strip()

        except Exception as error:
            last_error = error

            if _is_permanent(error):
                raise

            # Drop the cached client. The client-closed bug leaves a dead
            # client behind, and reusing it fails every following attempt.
            _CLIENTS.pop(api_key, None)

            if attempt < MAX_ATTEMPTS - 1:
                # Longer waits than usual: a 429 needs the rate window to pass.
                time.sleep((5 * (attempt + 1)) + random.uniform(0, 1))

    raise last_error if last_error else RuntimeError("Model call failed")


def _extract_json(text: str):
    """Pull a JSON object or array out of a reply that may be wrapped in prose."""
    cleaned = re.sub(r"^```(?:json)?", "", text.strip())
    cleaned = re.sub(r"```$", "", cleaned.strip()).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    for pattern in (r"\{.*\}", r"\[.*\]"):
        match = re.search(pattern, cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                continue

    return None


def _rules(language: str, term_style: str) -> str:
    style_rule = TERM_STYLES.get(term_style, TERM_STYLES[DEFAULT_STYLE])
    return f"Write in {language}. {style_rule}"


def _context(language: str, term_style: str) -> str:
    return f"""The transcript below is from a computer science lecture taught in
Urdu with English technical terms mixed in. That is normal, not an error.

{_rules(language, term_style)}

Use short sentences. Do not add any information that is not in the transcript."""


def study_notes(
    api_key: str,
    transcript: str,
    language: str = "English",
    term_style: str = DEFAULT_STYLE,
    question_count: int = 5,
) -> dict:
    """
    Summary, key points and practice questions in one request.

    Returns {"summary": str, "points": [str], "questions": [{question, answer}]}.
    Raises on a failed call. Returns an empty dict if the reply cannot be parsed,
    which is the caller's signal to fall back to separate calls.
    """
    prompt = f"""You are helping a university student revise a lecture.

{_context(language, term_style)}

Produce three things:
1. A summary. Start with one sentence saying what the lecture covered, then
   explain the main points in order.
2. The key points a student must remember.
3. {question_count} practice questions with answers. Every question must be
   answerable from the transcript alone.

Reply with JSON only. No explanation, no code fences. Use this shape:
{{"summary": "...",
  "points": ["...", "..."],
  "questions": [{{"question": "...", "answer": "..."}}]}}

TRANSCRIPT:
{transcript}"""

    data = _extract_json(_ask(api_key, prompt))

    if not isinstance(data, dict):
        return {}

    points = [str(p).strip() for p in data.get("points", []) if str(p).strip()]

    questions = [
        {"question": q.get("question", ""), "answer": q.get("answer", "")}
        for q in data.get("questions", [])
        if isinstance(q, dict)
    ]

    return {
        "summary": str(data.get("summary", "")).strip(),
        "points": points,
        "questions": questions,
    }


# --- Fallback: one call each, used only if the combined reply fails to parse ---

def summarize(
    api_key: str,
    transcript: str,
    language: str = "English",
    term_style: str = DEFAULT_STYLE,
) -> str:
    prompt = f"""You are helping a university student revise a lecture.

{_context(language, term_style)}

Write a summary. Start with one sentence saying what the lecture covered, then
explain the main points in order.

TRANSCRIPT:
{transcript}"""
    return _ask(api_key, prompt)


def key_points(
    api_key: str,
    transcript: str,
    language: str = "English",
    term_style: str = DEFAULT_STYLE,
) -> list[str]:
    prompt = f"""{_context(language, term_style)}

List the key points a student must remember. One point per line. No numbering,
no bullets, no extra text before or after the list.

TRANSCRIPT:
{transcript}"""
    raw = _ask(api_key, prompt)
    points = [line.strip(" -•*\t") for line in raw.split("\n")]
    return [p for p in points if p]


def practice_questions(
    api_key: str,
    transcript: str,
    count: int = 5,
    language: str = "English",
    term_style: str = DEFAULT_STYLE,
) -> list[dict]:
    prompt = f"""{_context(language, term_style)}

Write {count} practice questions that test whether a student understood the
lecture. Every question must be answerable from the transcript alone.

Reply with JSON only. No explanation, no code fences. Use this shape:
[{{"question": "...", "answer": "..."}}]

TRANSCRIPT:
{transcript}"""

    data = _extract_json(_ask(api_key, prompt))

    if not isinstance(data, list):
        return []

    return [
        {"question": item.get("question", ""), "answer": item.get("answer", "")}
        for item in data
        if isinstance(item, dict)
    ]
