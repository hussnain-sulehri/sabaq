"""
Teaching layer for Sabaq.

Takes the corrected transcript and produces study material:

- Summary
- Key points
- Practice questions

The transcript is a code-switched Urdu and English computer science lecture.
Prompts tell the model that English technical terms are expected, otherwise it
answers only in English and drops half the meaning.

Providers:
- Gemini runs first. It writes better Urdu than anything on Groq, and the
  Urdu notes mode is the feature that would suffer most from a weaker model.
- Groq is the fallback, because it is the larger floor. Gemini's free tier
  allows 20 generation requests per day per model; Groq's free tier allows
  hundreds to thousands. The fallback should be the provider still standing
  when the scarce one runs out, not the other way round.
- Either provider alone is enough to run the app. A missing key is skipped,
  not an error.

Model handling:
- Models are discovered with a listing call on both providers, which costs no
  generation quota.
- A generation probe is never used, because a probe on Gemini would spend one
  of twenty daily requests on nothing.
- When a model runs out, it is marked exhausted and the next model is used,
  then the next provider. The mark expires, so the app recovers on its own.
- GEMINI_MODEL and GROQ_MODEL pin one model per provider and skip selection.
"""

import json
import logging
import random
import re
import time

from google import genai
from google.genai import types

from config import get_setting

try:
    from groq import Groq

    GROQ_IMPORT_ERROR: str | None = None
except ImportError as error:  # the app still runs on Gemini alone
    Groq = None
    # Kept rather than swallowed. A missing package with a key set looks
    # identical to a dead key from the UI, and that cost a debugging session:
    # the fallback was configured, reported as configured, and never ran.
    GROQ_IMPORT_ERROR = str(error)

log = logging.getLogger(__name__)

GEMINI = "gemini"
GROQ = "groq"

# Gemini first: better Urdu. Groq second: far more daily headroom.
PROVIDER_ORDER = (GEMINI, GROQ)


# --------------------------------------------------
# Model preferences
# --------------------------------------------------

# Checked in this order. Models known to work for current keys come first,
# so a retired model never costs a round trip.
GEMINI_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
]

# Ordered by how well they write Urdu, not by speed or by daily allowance.
# llama-3.1-8b-instant has the largest quota and the weakest Urdu, which is
# exactly the failure the prompts exist to prevent, so it goes last.
GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "moonshotai/kimi-k2-instruct",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
]

PREFERRED_MODELS = {GEMINI: GEMINI_MODELS, GROQ: GROQ_MODELS}
MODEL_OVERRIDE_KEY = {GEMINI: "GEMINI_MODEL", GROQ: "GROQ_MODEL"}

# Gemini's limit is a daily one, so a spent model stays spent for a while.
GEMINI_COOLDOWN_SEC = 60 * 60

# Groq enforces requests per minute, requests per day, tokens per minute and
# tokens per day at once, and returns 429 for all four. A per-minute trip is
# over in seconds, so the default cooldown is short and the header wins when
# the API sends one.
GROQ_COOLDOWN_SEC = 60
GROQ_DAILY_COOLDOWN_SEC = 60 * 60

_CLIENTS: dict[str, object] = {}
_MODEL_CACHE: dict[str, str] = {}
_AVAILABLE_CACHE: dict[str, set[str]] = {}
_EXHAUSTED: dict[str, dict[str, float]] = {}  # scope -> model -> expiry


def _scope(provider: str, api_key: str) -> str:
    """Cache key. State is per provider and per key, never shared between them."""
    return f"{provider}:{api_key}"


# --------------------------------------------------
# Clients
# --------------------------------------------------

def _client(provider: str, api_key: str):
    """
    Keep one client per provider and key.

    Creating a client per call let the previous one be garbage collected
    mid-request, which closed the connection underneath an in-flight call.
    """
    scope = _scope(provider, api_key)

    if scope not in _CLIENTS:
        if provider == GEMINI:
            _CLIENTS[scope] = genai.Client(api_key=api_key)
        elif provider == GROQ:
            if Groq is None:
                raise RuntimeError("The groq package is not installed.")
            _CLIENTS[scope] = Groq(api_key=api_key)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    return _CLIENTS[scope]


# --------------------------------------------------
# Quota bookkeeping
# --------------------------------------------------

def _exhausted_models(scope: str) -> set[str]:
    """Models still inside their cooldown. Expired marks are dropped."""
    marks = _EXHAUSTED.get(scope)
    if not marks:
        return set()

    now = time.time()
    live = {model: until for model, until in marks.items() if until > now}
    _EXHAUSTED[scope] = live
    return set(live)


def _mark_exhausted(scope: str, model: str, seconds: float) -> None:
    _EXHAUSTED.setdefault(scope, {})[model] = time.time() + seconds
    _MODEL_CACHE.pop(scope, None)
    log.warning("%s is rate limited for %.0fs, moving on", model, seconds)


def _retry_after(error: Exception) -> float | None:
    """
    Seconds the API asked us to wait, when it says so.

    Groq returns a retry-after header. Honouring it is the difference between
    a model being unavailable for a minute and being written off for an hour.
    """
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)

    if not headers or not hasattr(headers, "get"):
        return None

    for name in ("retry-after", "Retry-After", "x-ratelimit-reset-requests"):
        raw = headers.get(name)
        if not raw:
            continue
        try:
            return max(1.0, float(str(raw).rstrip("s")))
        except ValueError:
            continue

    return None


def _cooldown(provider: str, error: Exception) -> float:
    """How long to leave a rate limited model alone."""
    asked = _retry_after(error)
    if asked:
        return asked

    if provider == GEMINI:
        return GEMINI_COOLDOWN_SEC

    text = str(error).lower()
    daily = "per day" in text or "rpd" in text or "tpd" in text
    return GROQ_DAILY_COOLDOWN_SEC if daily else GROQ_COOLDOWN_SEC


# --------------------------------------------------
# Model selection
# --------------------------------------------------

def _listed_models(provider: str, api_key: str) -> set[str]:
    """
    Model ids this key can see. Listing is not a generation request on either
    provider, so it does not touch the daily quota. An empty set means listing
    failed and the preference order is used blind.

    This matters more on Groq than on Gemini: Groq retires and reshuffles
    model ids often, and the live list is the only reliable source.
    """
    scope = _scope(provider, api_key)

    if scope in _AVAILABLE_CACHE:
        return _AVAILABLE_CACHE[scope]

    names: set[str] = set()

    try:
        if provider == GEMINI:
            for model in _client(GEMINI, api_key).models.list():
                name = str(getattr(model, "name", "") or "")
                if name:
                    names.add(name.replace("models/", ""))
        else:
            for model in _client(GROQ, api_key).models.list().data:
                name = str(getattr(model, "id", "") or "")
                if name:
                    names.add(name)
    except Exception:
        log.exception("%s model listing failed, using the preference order", provider)
        names = set()

    _AVAILABLE_CACHE[scope] = names
    return names


def get_available_model(provider: str, api_key: str) -> str:
    """
    Pick a model for this provider and key.

    Order: an explicit override, then the cached choice, then the first
    preferred model that is listed and not already rate limited.
    """
    override = get_setting(MODEL_OVERRIDE_KEY[provider], "").strip()
    if override:
        return override

    scope = _scope(provider, api_key)
    exhausted = _exhausted_models(scope)

    cached = _MODEL_CACHE.get(scope)
    if cached and cached not in exhausted:
        return cached

    listed = _listed_models(provider, api_key)

    for model in PREFERRED_MODELS[provider]:
        if model in exhausted:
            continue
        # When listing failed we have no list to check against, so try anyway.
        if listed and model not in listed:
            continue

        _MODEL_CACHE[scope] = model
        return model

    raise RuntimeError(
        f"No {provider} model available. Every model is either rate limited "
        "or not accessible with this key."
    )


def _order(force: str | None = None) -> tuple[str, ...]:
    """
    Provider order for this request.

    Pass force to pin one provider, which is how the two are compared on the
    same transcript. To route by language instead, return (GEMINI, GROQ) for
    Urdu and (GROQ, GEMINI) otherwise: that spends the plentiful quota on
    English and saves Gemini for the notes that need it.
    """
    if force in PROVIDER_ORDER:
        return (force,)
    return PROVIDER_ORDER


def active_backend(
    keys: dict[str, str | None], force: str | None = None
) -> tuple[str, str] | None:
    """
    The provider and model a request would use right now, for the sidebar.

    Returns None when nothing is reachable. Costs no generation quota.
    """
    for provider in _order(force):
        if not keys.get(provider):
            continue
        if provider == GROQ and Groq is None:
            continue
        try:
            return provider, get_available_model(provider, keys[provider])
        except Exception:
            continue
    return None


def provider_status(keys: dict[str, str | None]) -> list[dict]:
    """
    One row per provider: whether it can actually answer, and why not.

    The sidebar used to show only the backend that won. A provider with a
    key set but no package, or a key set and every model rate limited, was
    invisible. Costs no generation quota.
    """
    rows = []

    for provider in PROVIDER_ORDER:
        key = keys.get(provider)

        if not key:
            rows.append({"provider": provider, "ready": False,
                         "detail": "no key set"})
            continue

        if provider == GROQ and Groq is None:
            rows.append({
                "provider": provider,
                "ready": False,
                "detail": (
                    "key is set but the groq package is not installed "
                    f"({GROQ_IMPORT_ERROR}). Run: pip install groq"
                ),
            })
            continue

        try:
            rows.append({"provider": provider, "ready": True,
                         "detail": get_available_model(provider, key)})
        except Exception as error:
            rows.append({"provider": provider, "ready": False,
                         "detail": str(error)})

    return rows


# --------------------------------------------------
# Request handling
# --------------------------------------------------

# Retries for a flaky connection, per model.
MAX_TRANSIENT_ATTEMPTS = 3


def _max_switches(provider: str) -> int:
    """
    Moving to the next model is not a retry and must not eat the retry
    budget, or a key with three exhausted models would never reach the fourth.
    """
    return len(PREFERRED_MODELS[provider]) + 1


# Retrying these never helps.
PERMANENT_CODES = {400, 401, 403, 404}
PERMANENT_MARKERS = (
    "NOT_FOUND",
    "PERMISSION_DENIED",
    "INVALID_ARGUMENT",
    "UNAUTHENTICATED",
    "API key",
    "invalid_api_key",
)

QUOTA_CODES = {429}
QUOTA_MARKERS = ("RESOURCE_EXHAUSTED", "rate_limit_exceeded")


def _status_code(error: Exception) -> int | None:
    """
    HTTP status from an SDK error, when it carries one.

    Matching on the error text alone was fragile: a request id containing
    429 read as a quota failure.
    """
    for attribute in ("status_code", "code"):
        value = getattr(error, attribute, None)
        if isinstance(value, int):
            return value

    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _is_quota(error: Exception) -> bool:
    if _status_code(error) in QUOTA_CODES:
        return True
    return any(marker in str(error) for marker in QUOTA_MARKERS)


def _is_permanent(error: Exception) -> bool:
    code = _status_code(error)
    if code in PERMANENT_CODES:
        return True
    if code is not None:
        return False
    return any(marker in str(error) for marker in PERMANENT_MARKERS)


def _generate(provider: str, api_key: str, model: str, prompt: str, schema) -> str:
    """One call to one model. Everything provider-specific lives here."""
    if provider == GEMINI:
        config = (
            types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema[GEMINI],
            )
            if schema
            else None
        )
        response = _client(GEMINI, api_key).models.generate_content(
            model=model, contents=prompt, config=config
        )
        return (response.text or "").strip()

    # Groq, OpenAI-shaped. Best-effort mode rather than strict: strict needs
    # every field required with additionalProperties false and is only on
    # select models, and the Groq model list changes often.
    response_format = (
        {
            "type": "json_schema",
            "json_schema": {
                "name": "study_notes",
                "strict": False,
                "schema": schema[GROQ],
            },
        }
        if schema
        else None
    )

    completion = _client(GROQ, api_key).chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format=response_format,
        temperature=0.3,
    )
    return (completion.choices[0].message.content or "").strip()


def _ask_provider(provider: str, api_key: str, prompt: str, schema) -> tuple[str, str]:
    """
    Send a prompt to one provider, moving to the next model when one is
    rate limited.

    A quota error is never retried on the same model. On Gemini that would
    spend two more of the twenty daily requests to learn nothing.
    """
    last_error: Exception | None = None

    for _ in range(_max_switches(provider)):
        model = get_available_model(provider, api_key)
        scope = _scope(provider, api_key)

        for attempt in range(MAX_TRANSIENT_ATTEMPTS):
            try:
                text = _generate(provider, api_key, model, prompt, schema)
                return text, f"{provider} / {model}"

            except Exception as error:
                last_error = error

                if _is_quota(error):
                    _mark_exhausted(scope, model, _cooldown(provider, error))
                    break  # next model, without spending a retry

                if _is_permanent(error):
                    raise

                log.warning(
                    "Transient failure on %s, attempt %s: %s",
                    model, attempt + 1, error,
                )

                # A dead client fails every following attempt, so drop it.
                _CLIENTS.pop(scope, None)

                if attempt < MAX_TRANSIENT_ATTEMPTS - 1:
                    time.sleep((5 * (attempt + 1)) + random.uniform(0, 1))
        else:
            # Ran out of retries rather than quota. Another model will not help.
            break

    raise last_error if last_error else RuntimeError(f"{provider} request failed")


def _ask(
    keys: dict[str, str | None],
    prompt: str,
    schema=None,
    force: str | None = None,
) -> tuple[str, str]:
    """
    Send a prompt, falling through to the next provider when one is spent.

    Returns (text, backend_label) so the caller can show which model actually
    answered. A silent fallback looks like nothing happened.
    """
    last_error: Exception | None = None
    tried = False

    for provider in _order(force):
        if not keys.get(provider):
            continue
        if provider == GROQ and Groq is None:
            log.warning("GROQ_API_KEY is set but the groq package is missing")
            continue

        tried = True

        try:
            return _ask_provider(provider, keys[provider], prompt, schema)
        except Exception as error:
            last_error = error
            log.warning("Provider %s failed, trying the next one: %s", provider, error)

    if not tried:
        raise RuntimeError(
            "No note generator configured. Set GEMINI_API_KEY or GROQ_API_KEY."
        )

    raise last_error if last_error else RuntimeError("Every provider failed")


# --------------------------------------------------
# Reply shape
# --------------------------------------------------

# The same structure in two dialects. Gemini wants the uppercase type enum,
# Groq wants ordinary JSON Schema.
NOTES_SCHEMA = {
    GEMINI: {
        "type": "OBJECT",
        "properties": {
            "summary": {"type": "STRING"},
            "points": {"type": "ARRAY", "items": {"type": "STRING"}},
            "questions": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "question": {"type": "STRING"},
                        "answer": {"type": "STRING"},
                    },
                    "required": ["question", "answer"],
                },
            },
        },
        "required": ["summary", "points", "questions"],
    },
    GROQ: {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "points": {"type": "array", "items": {"type": "string"}},
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "answer": {"type": "string"},
                    },
                    "required": ["question", "answer"],
                },
            },
        },
        "required": ["summary", "points", "questions"],
    },
}


def _parse(text: str):
    """
    Read a JSON reply.

    Schema mode makes a clean parse the normal case. The fence strip stays as
    cheap insurance, because the alternative on failure is more requests out
    of a budget that may be down to twenty a day.
    """
    if not text:
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    cleaned = re.sub(r"^```(?:json)?", "", text.strip())
    cleaned = re.sub(r"```$", "", cleaned.strip()).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        log.error("Reply was not valid JSON: %.200s", text)
        return None


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
# Note generation
# --------------------------------------------------

def study_notes(
    keys: dict[str, str | None],
    transcript: str,
    language: str = "English",
    term_style: str = DEFAULT_STYLE,
    question_count: int = 5,
    force: str | None = None,
) -> dict:
    """
    Summary, key points and questions in one request.

    One request instead of three matters: Gemini's free tier allows 20 per
    day. The returned dict carries 'backend', the provider and model that
    answered, so a fallback is visible rather than silent.
    """
    # The word JSON stays in the prompt on purpose. Groq's older json_object
    # mode refuses a request without it, and it costs nothing in schema mode.
    prompt = f"""You are helping a university student revise a lecture.

{_context(language, term_style)}

Create, as JSON:
1. A summary. One sentence saying what the lecture covered, then the main
   points in the order they were taught.
2. The key points a student must remember, one per entry.
3. {question_count} practice questions, each with its answer.

Every question must be answerable from the transcript alone.
Do not invent examples, numbers or definitions the teacher did not give.

TRANSCRIPT:
{transcript}"""

    text, backend = _ask(keys, prompt, schema=NOTES_SCHEMA, force=force)
    data = _parse(text)

    if not isinstance(data, dict):
        return {}

    return {
        "backend": backend,
        "summary": str(data.get("summary", "")).strip(),
        "points": [
            str(point).strip()
            for point in data.get("points", [])
            if str(point).strip()
        ],
        "questions": [
            {
                "question": str(item.get("question", "")).strip(),
                "answer": str(item.get("answer", "")).strip(),
            }
            for item in data.get("questions", [])
            if isinstance(item, dict) and str(item.get("question", "")).strip()
        ],
    }

# --------------------------------------------------
# Asking the lecture a question
# --------------------------------------------------

ANSWER_SCHEMA = {
    GEMINI: {
        "type": "OBJECT",
        "properties": {
            "answer": {"type": "STRING"},
            "in_lecture": {"type": "BOOLEAN"},
            "basis": {"type": "STRING"},
        },
        "required": ["answer", "in_lecture", "basis"],
    },
    GROQ: {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "in_lecture": {"type": "boolean"},
            "basis": {"type": "string"},
        },
        "required": ["answer", "in_lecture", "basis"],
    },
}


def answer_question(
    keys: dict[str, str | None],
    transcript: str,
    question: str,
    language: str = "Urdu",
    term_style: str = DEFAULT_STYLE,
    force: str | None = None,
) -> dict:
    """
    Answer a student's question from the lecture transcript alone.

    'in_lecture' is the point. A model asked about overfitting will answer
    from its own knowledge whether or not the teacher covered it, and a
    student cannot tell the difference. Saying which sentence an answer came
    from, or admitting the teacher never said it, is what separates a
    revision aid from a chatbot wearing a lecture as a costume.

    The question is spoken Urdu with English technical terms, so it has been
    through the normalizer too and reads the same way the transcript does.
    """
    prompt = f"""A student is asking about a lecture they just attended.

{_context(language, term_style)}

Answer only from the transcript. If the teacher did not cover it, say so in
one sentence and do not answer from your own knowledge.

Return, as JSON:
- answer: the reply to read out to the student. Two or three sentences.
- in_lecture: true if the transcript answers the question, false if not.
- basis: the part of the transcript the answer rests on, in the transcript's
  own words. Empty when in_lecture is false.

TRANSCRIPT:
{transcript}

QUESTION:
{question}"""

    text, backend = _ask(keys, prompt, schema=ANSWER_SCHEMA, force=force)
    data = _parse(text)

    if not isinstance(data, dict):
        return {}

    return {
        "backend": backend,
        "answer": str(data.get("answer", "")).strip(),
        "in_lecture": bool(data.get("in_lecture", False)),
        "basis": str(data.get("basis", "")).strip(),
    }