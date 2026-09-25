"""
Term normalizer for Sabaq.

The Urdu model writes English technical terms in Urdu script, and it spells
the same term differently each time. This module maps those spellings back
to the correct English term so the transcript stays searchable.

Every variant below was observed in real output, not invented. Two speech
models produced them and they do not agree: the batch API and the streaming
model whisper-rt transliterate the same word differently, so both spellings
are kept. Where a variant is in Devanagari it came from a turn the streaming
model decided was Hindi, and it is matched directly rather than transliterated
into Urdu script first.

Matching rules:
- Replacement is boundary anchored. A plain string replace turns بہترین
  ("best") into بہ + "train", because ترین sits inside it. Nothing in the
  glossary may match a fragment of a longer Urdu word.
- Longest variant first, so 'training data' is consumed before 'data'.
- The fuzzy pass edits Urdu runs in place, leaving punctuation, line breaks
  and Latin text exactly where they were.
"""

import difflib
import re
from collections import Counter

from subject_glossaries import SUBJECT_GLOSSARIES

# Unicode blocks. Arabic covers Urdu. Devanagari appears when the streaming
# model decides a turn is Hindi. Observed Devanagari spellings are in the
# glossary and match through the same pass (finding 16); unseen ones are
# missed exactly as unseen Urdu spellings are.
ARABIC = r"\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF"
DEVANAGARI = r"\u0900-\u097F"

# What counts as "inside a word" for boundary checks. Python's \w does not
# include combining vowel signs, so डाट matched inside डाटाबेस ("database")
# because the ा after it was not seen as part of the word. Found in
# evaluation. Vowel signs and Urdu diacritics are added here; the Devanagari
# full stops । and ॥ are left out so a variant before them still matches.
WORD_CHARS = (
    r"\w\u0900-\u0963\u0966-\u097F"               # Devanagari, minus । ॥
    r"\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED"  # Arabic-script marks
)

# Canonical English term -> spellings seen in output.
#
# Sources, so the list can be audited rather than trusted:
#   batch  - AssemblyAI async transcription, three lectures
#   live   - whisper-rt streaming, same audio, different spellings
#   deva   - Devanagari, from turns the streaming model labelled Hindi
GLOSSARY: dict[str, list[str]] = {
    # Observed in the database lecture, where ڈاٹا بیس had been corrected to
    # "data بیس": half a term, counted as a correction. Longest match first
    # now takes the compound before the shorter "data" entry sees it.
    "database": ["ڈاٹا بیس"],
    "machine learning": [
        "مشین لرنڈنگ", "مشین لرننگ", "مشین لرنگ",
        "ماشین لرننگ",                                    # live
    ],
    "gradient descent": ["گریڈینڈ ڈیسینڈز", "گریڈیئنٹ ڈیسنٹ", "گریڈینٹ ڈیسنٹ"],
    "early stopping": ["ارلی سٹوپنگ", "ارلی سٹاپنگ", "ارلی اسٹاپنگ"],
    "learning rate": ["لرننگ ریڈ", "لرننگ ریٹ", "لرنگ ریٹ"],
    "training data": [
        "ترین ڈیٹا", "ترینڈ ڈیڈا", "ٹریننگ ڈیٹا", "ترینڈ ڈیٹا",
        "ट्रेनिंग डैटा", "ट्रेनिंग डाटा",                  # deva
    ],
    "training accuracy": ["تریننگ ایکوریسی", "ترینڍ ایکوریسی"],
    "test accuracy": ["تیسٹ ایکوری سی", "ٹیسٹ ایکوریسی", "تیسٹ ایکوریسی"],
    "regularization": [
        "ریگلریزیشنز", "ریگلریزیشنس", "ریگولرائزیشن", "ریگولرائزیشنز",
        "ڑیگلوریزیشن",                                     # live
        "रेगुलराइजेशन",                                     # deva
    ],
    "overfitting": [
        "آور فٹنگ", "اوور فٹنگ", "اوورفٹنگ", "آوورفٹنگ",
        # live. Two words, and the first is the ordinary Urdu word 'and',
        # so it is only safe as part of the pair, never on its own.
        "اور فٹنگ",
        "ओवरफिटिंग",                                       # deva
    ],
    "dropout": ["ڈروپ اوٹ", "ڈراپ آؤٹ", "ڈراپ اوٹ", "ڈروپ آؤٹ"],
    "understand": ["انڈرسینڈ", "انڈرسٹینڈ"],
    "important": ["امپورٹن", "امپورٹنٹ", "ایمپورٹن"],      # last: live
    "accuracy": ["ایکوری سی", "ایکوریسی", "ایکیوریسی"],
    "training": [
        "تریننگ", "ترینڍ", "ٹریننگ", "ترینڈ",
        "ٹرینگ",                                           # live
        "ट्रेनिंग",                                         # deva
    ],
    "students": [
        "سٹوڈنٹس", "اسٹوڈنٹس",
        "سٹوڈنس",                                          # live
        "स्टोडन्स", "स्टूडेंट्स",                            # deva
    ],
    "solution": ["سلوشن", "سولوشن"],
    # فیصد removed: it is the native Urdu word, not a mangled spelling of
    # "percent". The glossary holds transliterations only.
    "percent": ["پرسنٹ"],
    "lecture": ["لیکچر", "لیکچرز"],
    "pattern": [
        "پیٹرڈ", "پیٹرن", "پیٹرنز",
        "پہٹرین",                                          # live
        "पैठरन", "पैटर्न",                                  # deva
    ],
    "model": [
        "موڈڈل", "موڈول", "موڈل", "ماڈل",
        "मोडल", "मॉडल",                                    # deva
    ],
    "topic": ["ٹاپک", "ٹاپِک"],
    "train": ["ترین", "ٹرین"],
    "data": [
        "ڈیڈا", "ڈیٹا", "ڈاٹا",
        "डाट", "डैटा", "डाटा",                              # deva
    ],
    "learn": ["لرن"],
    "test": ["تیسٹ", "ٹیسٹ"],
    "L2": ["ایل ٹو", "ایل۔ٹو"],
}

# Everyday English words, as opposed to subject vocabulary. Reported
# separately so a headline count of technical corrections is not inflated by
# "students" and "lecture".
GENERAL_TERMS = frozenset({
    "students", "lecture", "topic", "important", "understand", "solution",
    "learn", "percent",
})

# Contexts in which a variant is ordinary Urdu rather than a term. ترین alone
# is also the superlative suffix, written as a separate word in اہم ترین
# ("most important"), and would otherwise become "اہم train".
GUARDS: dict[str, list[str]] = {
    "ترین": ["اہم", "کم", "مشکل", "آسان", "جدید", "تیز", "قریب", "عظیم",
             "مضبوط", "بلند", "بڑا", "بڑے"],
}

# Ordinary Urdu words that sit close enough to a transliteration to be
# corrupted by the fuzzy pass. تین (three) scoring 0.86 against ترین (train)
# is the documented failure; these are held out of fuzzy matching entirely.
STOPWORDS = frozenset(
    """
    تین دو ایک سب ہم ہے ہیں ہو ہوتا ہوتی کا کی کے کو میں سے یہ وہ اور تو جو
    پر نہیں آج بہت کرتا کرتی کرتے لیکن اس ان کہ بھی گے کیا جب پھر لیے لئے
    طور مثال یعنی اگلے اپنے ہمارے پاس کبھی زیادہ پہلا دوسرا تیسرا وقت نام
    """.split()
) | frozenset(
    # The same function words in Devanagari. Without these, every ordinary
    # Hindi word in a flipped turn is offered as an uncovered technical term,
    # which buries the handful that actually are one.
    """
    है हैं हो होता होती का की के को में से यह वह ये वो और तो जो पर नहीं आज
    बहुत करता करती करते लेकिन इस उस इन उन कि भी गे क्या जब फिर लिए तौर
    मिसाल यानी अगले अपने हमारे पास कभी ज्यादा पहला दूसरा तीसरा वक्त नाम
    इसको ऐसी सूरत रह देते कहते चाहते सिर्फ सारी अब एक दो तीन सब हम
    """.split()
)


def _compile_pairs(
    glossary: dict[str, list[str]] | None = None,
) -> list[tuple[str, str, re.Pattern]]:
    """
    Flatten the glossary into (variant, english, pattern) triples, longest
    variant first.

    Longest first matters: 'training data' must be replaced before 'data',
    otherwise the shorter match eats part of the longer phrase.

    The pattern is anchored with word-boundary lookarounds over WORD_CHARS,
    which is \\w plus the vowel signs and diacritics \\w leaves out. This
    stops a variant matching inside a longer Urdu or Devanagari word without
    needing a separate tokenizer.
    """
    glossary = GLOSSARY if glossary is None else glossary
    flat = list({
        (variant, english)
        for english, variants in glossary.items()
        for variant in variants
    })
    flat.sort(key=lambda pair: len(pair[0]), reverse=True)

    def pattern(variant: str) -> re.Pattern:
        # One fixed-width lookbehind per guard word, since Python does not
        # allow variable-width lookbehind.
        guards = "".join(
            rf"(?<!{re.escape(word)}\s)" for word in GUARDS.get(variant, [])
        )
        return re.compile(
            rf"(?<![{WORD_CHARS}]){guards}{re.escape(variant)}(?![{WORD_CHARS}])"
        )

    return [(variant, english, pattern(variant)) for variant, english in flat]


PAIRS = _compile_pairs()


def glossary_for(subjects: tuple[str, ...] | list[str] = ()) -> dict[str, list[str]]:
    """The AI glossary plus the chosen starter glossaries."""
    merged = {english: list(variants) for english, variants in GLOSSARY.items()}
    for subject in subjects:
        for english, variants in SUBJECT_GLOSSARIES[subject].items():
            merged.setdefault(english, []).extend(variants)
    return merged


_PAIR_CACHE: dict[tuple, list] = {}


def _pairs(subjects) -> list[tuple[str, str, re.Pattern]]:
    """Compiled pairs for a glossary choice, built once and kept."""
    if not subjects:
        return PAIRS
    key = tuple(sorted(subjects))
    if key not in _PAIR_CACHE:
        _PAIR_CACHE[key] = _compile_pairs(glossary_for(subjects))
    return _PAIR_CACHE[key]

# every known variant, used by the fuzzy pass
ALL_VARIANTS = [variant for variant, _, _ in PAIRS]
VARIANT_TO_ENGLISH = {variant: english for variant, english, _ in PAIRS}

# Multi-word variants cannot be reached by a single-token fuzzy match, so
# comparing against them only wastes time and widens the chance of a bad hit.
SINGLE_WORD_VARIANTS = [v for v in ALL_VARIANTS if " " not in v]

_URDU_RUN = re.compile(rf"[{ARABIC}]+")
_DEVANAGARI_RUN = re.compile(rf"[{DEVANAGARI}]+")
_DEVANAGARI = re.compile(rf"[{DEVANAGARI}]")
_LATIN = re.compile(r"[A-Za-z]")

# Either script, for the leftover report. The glossary now holds variants in
# both, so a leftover report that reads only one script hides half of what
# the glossary still needs.
_WORD_RUN = re.compile(rf"[{ARABIC}{DEVANAGARI}]+")


def normalize(
    text: str, subjects: tuple[str, ...] | list[str] = ()
) -> tuple[str, list[dict]]:
    """
    Replace known Urdu-script and Devanagari spellings with the English term.

    With no subjects this is the frozen AI glossary, exactly as evaluated.
    Returns the corrected text and a log of what was changed, so the
    corrections can be shown to the user instead of happening silently.
    """
    if not text:
        return text, []

    corrected = text
    changes = []

    for variant, english, pattern in _pairs(subjects):
        corrected, count = pattern.subn(english, corrected)
        if count:
            changes.append(
                {"found": variant, "replaced_with": english, "times": count,
                 "method": "exact"}
            )

    return corrected, changes


def fuzzy_pass(
    text: str, threshold: float = 0.82, min_length: int = 3
) -> tuple[str, list[dict]]:
    """
    Second pass for spellings the glossary has not seen yet.

    Each remaining Urdu word is compared against every known single-word
    variant. A close match above the threshold is treated as the same term.
    Run this after normalize(), never instead of it.

    Only Urdu runs are touched, so punctuation, digits, line breaks and any
    Latin text come through byte for byte.
    """
    if not text:
        return text, []

    hits: list[tuple[str, str, float]] = []

    def replace(match: re.Match) -> str:
        token = match.group(0)

        if len(token) < min_length or token in STOPWORDS:
            return token

        close = difflib.get_close_matches(
            token, SINGLE_WORD_VARIANTS, n=1, cutoff=threshold
        )
        if not close:
            return token

        english = VARIANT_TO_ENGLISH[close[0]]
        ratio = difflib.SequenceMatcher(None, token, close[0]).ratio()
        hits.append((token, english, ratio))
        return english

    corrected = _URDU_RUN.sub(replace, text)

    # One row per distinct token, not one per occurrence.
    counts = Counter((token, english) for token, english, _ in hits)
    scores = {(token, english): ratio for token, english, ratio in hits}

    changes = [
        {"found": token, "replaced_with": english, "times": times,
         "method": f"fuzzy ({scores[(token, english)]:.2f})"}
        for (token, english), times in counts.items()
    ]

    return corrected, changes


def clean_transcript(
    text: str,
    use_fuzzy: bool = True,
    subjects: tuple[str, ...] | list[str] = (),
) -> tuple[str, list[dict]]:
    """
    Run both passes and return the corrected text with a combined change log.

    With no subjects this is the frozen AI glossary, exactly as evaluated.
    """
    corrected, changes = normalize(text, subjects)

    if use_fuzzy:
        corrected, fuzzy_changes = fuzzy_pass(corrected)
        changes = changes + fuzzy_changes

    return corrected, changes


def unknown_terms(text: str, min_length: int = 3) -> list[dict]:
    """
    Words left over after correction, most frequent first, with their script.

    Run this on the corrected transcript. Anything here is either ordinary
    vocabulary or a transliteration the glossary has not met yet, which is how
    the glossary is meant to grow: by reading real output, not by guessing.

    Both scripts are read. Reporting Urdu only meant that the half of a
    flipped transcript the app warns cannot be corrected was also the half
    hidden from the one view whose job is to say what to add next.
    """
    if not text:
        return []

    counts = Counter(
        token
        for token in _WORD_RUN.findall(text)
        if len(token) >= min_length and token not in STOPWORDS
    )

    return [
        {
            "term": token,
            "times": times,
            "script": "devanagari" if _DEVANAGARI.search(token) else "urdu",
        }
        for token, times in counts.most_common()
    ]


def term_count(text: str) -> int:
    """
    How many known technical terms appear in a piece of text, in any form.

    Counted through the same longest-match pass as normalize(), so a
    'training data' hit is one term and not three.
    """
    _, changes = normalize(text)
    return sum(change["times"] for change in changes)


def script_mix(text: str) -> dict:
    """
    Characters per writing system.

    The database lecture flipped from Urdu script to Devanagari mid-sentence
    and never switched back. Only Devanagari spellings already observed are
    in the glossary, so most of a flipped section is still uncorrected.
    Surfacing the count is the difference between a silent miss and a stated
    limit.
    """
    return {
        "urdu": sum(len(run) for run in _URDU_RUN.findall(text)),
        "devanagari": len(_DEVANAGARI.findall(text)),
        "latin": len(_LATIN.findall(text)),
    }


def has_devanagari(text: str) -> bool:
    """True when part of the transcript is in Devanagari, which the glossary covers only partly."""
    return bool(_DEVANAGARI.search(text))