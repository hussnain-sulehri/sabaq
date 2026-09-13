"""
Teaching layer for Sabaq.

Takes the corrected transcript and produces study material:

- Summary
- Key points
- Practice questions

The transcript is a code-switched Urdu and English computer science lecture.
Prompts tell Gemini that English technical terms are expected, otherwise it
answers only in English and drops half the meaning.

Model handling:
- Models are discovered with a listing call, which costs no generation quota.
- A generation probe is never used, because the free tier allows 20 generation
  requests per day and a probe would spend one of them on nothing.
- When a model's daily quota runs out, it is marked exhausted and the next
  model in the list is used automatically.
- GEMINI_MODEL in .env or Streamlit secrets pins one model and skips all of it.
"""

import json
import random
import re
import time

from google import genai

from config import get_setting

# --------------------------------------------------
# Model selection
# --------------------------------------------------

# Checked in this order. Models known to work for current keys come first,
# so a retired model never costs a round trip.
PREFERRED_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
]

_CLIENTS: dict[str, genai.Client] = {}
_MODEL_CACHE: dict[str, str] = {}
_AVAILABLE_CACHE: dict[str, set[str]] = {}
_EXHAUSTED: dict[str, set[str]] = {}


def _client(api_key: str) -> genai.Client:
    """
    Keep one client per API key.

    Creating a client per call let the previous one be garbage collected
    mid-request, which closed the connection underneath an in-flight call.
    """
    if api_key not in _CLIENTS:
        _CLIENTS[api_key] = genai.Client(api_key=api_key)
    return _CLIENTS[api_key]


def _listed_models(api_key: str) -> set[str]:
    """
    Model ids this key can see. Listing is not a generation request, so it
    does not touch the daily quota. An empty set means listing failed and
    the preference order is used blind.
    """
    if api_key in _AVAILABLE_CACHE:
        return _AVAILABLE_CACHE[api_key]

    names: set[str] = set()

    try:
        for model in _client(api_key).models.list():
            name = str(getattr(model, "name", "") or "")
            if name:
                names.add(name.replace("models/", ""))
    except Exception:
        names = set()

    _AVAILABLE_CACHE[api_key] = names
    return names


def get_available_model(api_key: str) -> str:
    """
    Pick a model for this key.

    Order: an explicit override, then the cached choice, then the first
    preferred model that is listed and not already out of quota.
    """
    override = get_setting("GEMINI_MODEL", "").strip()
    if override:
        return override

    exhausted = _EXHAUSTED.get(api_key, set())

    cached = _MODEL_CACHE.get(api_key)
    if cached and cached not in exhausted:
        return cached

    listed = _listed_models(api_key)

    for model in PREFERRED_MODELS:
        if model in exhausted:
            continue
        # When listing failed we have no list to check against, so try anyway.
        if listed and model not in listed:
            continue

        _MODEL_CACHE[api_key] = model
        return model

    raise RuntimeError(
        "No Gemini model available. Every model is either out of daily quota "
        "or not accessible with this key."
    )


# --------------------------------------------------
# Request handling
# --------------------------------------------------

MAX_ATTEMPTS = 3

# Retrying these never helps.
PERMANENT = (
    "NOT_FOUND",
    "PERMISSION_DENIED",
    "INVALID_ARGUMENT",
    "UNAUTHENTICATED",
    "API key",
)

QUOTA = ("RESOURCE_EXHAUSTED", "429")


def _is_permanent(error: Exception) -> bool:
    return any(marker in str(error) for marker in PERMANENT)


def _is_quota(error: Exception) -> bool:
    return any(marker in str(error) for marker in QUOTA)


def _ask(api_key: str, prompt: str) -> str:
    """
    Send a prompt, moving to the next model when one runs out of quota.

    A quota error is never retried on the same model. Retrying it would
    spend two more of the twenty daily requests to learn nothing.
    """
    last_error: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        model = get_available_model(api_key)

        try:
            response = _client(api_key).models.generate_content(
                model=model, contents=prompt
            )
            return (response.text or "").strip()

        except Exception as error:
            last_error = error

            if _is_quota(error):
                # This model is done for the day. Try the next one at once.
                _EXHAUSTED.setdefault(api_key, set()).add(model)
                _MODEL_CACHE.pop(api_key, None)
                continue

            if _is_permanent(error):
                raise

            # A dead client fails every following attempt, so drop it.
            _CLIENTS.pop(api_key, None)

            if attempt < MAX_ATTEMPTS - 1:
                time.sleep((5 * (attempt + 1)) + random.uniform(0, 1))

    raise last_error if last_error else RuntimeError("Gemini request failed")


# --------------------------------------------------
# Term styles
# --------------------------------------------------

TERM_STYLES = {
    "Keep terms in English": (
        "Keep every technical term in English, in Latin script, exactly as a "
        "computer science textbook writes it, in lowercase unless it is a "
        "proper noun. Write everything else in the target language."
    ),
    "Translate terms to Urdu": (
        "Translate technical terms into Urdu as well. Use standard Urdu "
        "academic terminology where it exists. If no standard term exists, "
        "explain clearly in Urdu."
    ),
    "Urdu term, English in brackets": (
        "Write technical terms in Urdu followed by the English term in "
        "brackets the first time they appear. After that use the Urdu term."
    ),
}

DEFAULT_STYLE = "Keep terms in English"


def _extract_json(text: str):
    """Pull JSON out of a reply that may be wrapped in fences or prose."""
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
    style = TERM_STYLES.get(term_style, TERM_STYLES[DEFAULT_STYLE])
    return f"Write in {language}. {style}"


def _context(language: str, term_style: str) -> str:
    return f"""The transcript below is from a computer science lecture.
The teacher speaks Urdu mixed with English technical terms. This is normal.

{_rules(language, term_style)}

Use short sentences.
Do not add information that is not present in the transcript."""


# --------------------------------------------------
# Combined call
# --------------------------------------------------

def study_notes(
    api_key: str,
    transcript: str,
    language: str = "English",
    term_style: str = DEFAULT_STYLE,
    question_count: int = 5,
) -> dict:
    """
    Summary, key points and questions in one request.

    One request instead of three matters: the free tier allows 20 per day.
    Returns an empty dict if the reply cannot be parsed, which tells the
    caller to fall back to separate calls.
    """
    prompt = f"""You are helping a university student revise a lecture.

{_context(language, term_style)}

Create:
1. A summary
2. Key points
3. {question_count} practice questions with answers

Every question must be answerable from the transcript alone.

Return JSON only, no code fences:
{{"summary": "", "points": [], "questions": [{{"question": "", "answer": ""}}]}}

TRANSCRIPT:
{transcript}"""

    data = _extract_json(_ask(api_key, prompt))

    if not isinstance(data, dict):
        return {}

    return {
        "summary": str(data.get("summary", "")).strip(),
        "points": [
            str(x).strip() for x in data.get("points", []) if str(x).strip()
        ],
        "questions": [
            {"question": q.get("question", ""), "answer": q.get("answer", "")}
            for q in data.get("questions", [])
            if isinstance(q, dict)
        ],
    }


# --------------------------------------------------
# Fallbacks, used only when the combined reply will not parse
# --------------------------------------------------

def summarize(
    api_key: str,
    transcript: str,
    language: str = "English",
    term_style: str = DEFAULT_STYLE,
) -> str:
    return _ask(api_key, f"""{_context(language, term_style)}

Write a short lecture summary. Start with one sentence saying what the
lecture covered, then explain the main points in order.

TRANSCRIPT:
{transcript}""")


def key_points(
    api_key: str,
    transcript: str,
    language: str = "English",
    term_style: str = DEFAULT_STYLE,
) -> list[str]:
    result = _ask(api_key, f"""{_context(language, term_style)}

List the key points a student must remember. One point per line.
No numbering, no bullets, no text before or after the list.

TRANSCRIPT:
{transcript}""")

    return [line.strip(" -•*\t") for line in result.split("\n") if line.strip()]


def practice_questions(
    api_key: str,
    transcript: str,
    count: int = 5,
    language: str = "English",
    term_style: str = DEFAULT_STYLE,
) -> list[dict]:
    data = _extract_json(_ask(api_key, f"""{_context(language, term_style)}

Create {count} questions with answers. Every question must be answerable
from the transcript alone.

Return JSON only, no code fences:
[{{"question": "", "answer": ""}}]

TRANSCRIPT:
{transcript}"""))

    if not isinstance(data, list):
        return []

    return [
        {"question": item.get("question", ""), "answer": item.get("answer", "")}
        for item in data
        if isinstance(item, dict)
    ]
