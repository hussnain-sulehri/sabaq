"""
Teaching layer for Sabaq.

Takes the corrected transcript and produces study material:
a summary, key points, and practice questions.

The transcript is code-switched Urdu and English, so every prompt says so.
Without that instruction the model tends to answer only in English and
drops half the meaning.
"""

import json
import re

from google import genai

MODEL = "gemini-3.6-flash"

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


def _ask(api_key: str, prompt: str) -> str:
    client = _client(api_key)
    response = client.models.generate_content(model=MODEL, contents=prompt)
    return (response.text or "").strip()


def _strip_fences(text: str) -> str:
    """Models often wrap JSON in code fences. Remove them before parsing."""
    text = re.sub(r"^```(?:json)?", "", text.strip())
    text = re.sub(r"```$", "", text.strip())
    return text.strip()


def _rules(language: str, term_style: str) -> str:
    style_rule = TERM_STYLES.get(term_style, TERM_STYLES[DEFAULT_STYLE])
    return f"Write in {language}. {style_rule}"


def summarize(
    api_key: str,
    transcript: str,
    language: str = "English",
    term_style: str = DEFAULT_STYLE,
) -> str:
    prompt = f"""You are helping a university student revise a lecture.

The transcript below is from a computer science lecture taught in Urdu with
English technical terms mixed in. That is normal, not an error.

{_rules(language, term_style)}

Use short sentences. Do not add information that is not in the transcript.

Start with one sentence saying what the lecture covered, then explain the main
points in order.

TRANSCRIPT:
{transcript}"""
    return _ask(api_key, prompt)


def key_points(
    api_key: str,
    transcript: str,
    language: str = "English",
    term_style: str = DEFAULT_STYLE,
) -> list[str]:
    prompt = f"""Read this lecture transcript, taught in Urdu with English
technical terms mixed in.

List the key points a student must remember.

{_rules(language, term_style)}

One point per line. No numbering, no bullets, no extra text before or after
the list.

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
    """Return a list of {question, answer} built only from the transcript."""
    prompt = f"""Read this lecture transcript, taught in Urdu with English
technical terms mixed in.

Write {count} practice questions that test whether a student understood the
lecture. Every question must be answerable from the transcript alone. Do not
invent material that was not taught.

{_rules(language, term_style)}

Reply with JSON only. No explanation, no code fences. Use this shape:
[{{"question": "...", "answer": "..."}}]

TRANSCRIPT:
{transcript}"""

    raw = _strip_fences(_ask(api_key, prompt))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Retry once by pulling the first JSON array out of the reply.
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []

    return [
        {"question": item.get("question", ""), "answer": item.get("answer", "")}
        for item in data
        if isinstance(item, dict)
    ]
