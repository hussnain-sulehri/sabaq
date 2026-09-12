"""
Term normalizer for Sabaq.

The Urdu model writes English technical terms in Urdu script, and it spells
the same term differently each time. This module maps those spellings back
to the correct English term so the transcript stays searchable.

Every variant below was observed in real output, not invented.
"""

import difflib
import re

# canonical English term -> Urdu-script spellings seen in output
GLOSSARY: dict[str, list[str]] = {
    "machine learning": ["مشین لرنڈنگ", "مشین لرننگ", "مشین لرنگ"],
    "gradient descent": ["گریڈینڈ ڈیسینڈز", "گریڈیئنٹ ڈیسنٹ", "گریڈینٹ ڈیسنٹ"],
    "early stopping": ["ارلی سٹوپنگ", "ارلی سٹاپنگ", "ارلی اسٹاپنگ"],
    "learning rate": ["لرننگ ریڈ", "لرننگ ریٹ", "لرنگ ریٹ"],
    "training data": ["ترین ڈیٹا", "ترینڈ ڈیڈا", "ٹریننگ ڈیٹا", "ترینڈ ڈیٹا"],
    "training accuracy": ["تریننگ ایکوریسی", "ترینڍ ایکوریسی"],
    "test accuracy": ["تیسٹ ایکوری سی", "ٹیسٹ ایکوریسی", "تیسٹ ایکوریسی"],
    "regularization": ["ریگلریزیشنز", "ریگلریزیشنس", "ریگولرائزیشن", "ریگولرائزیشنز"],
    "overfitting": ["آور فٹنگ", "اوور فٹنگ", "اوورفٹنگ", "آوورفٹنگ"],
    "dropout": ["ڈروپ اوٹ", "ڈراپ آؤٹ", "ڈراپ اوٹ", "ڈروپ آؤٹ"],
    "understand": ["انڈرسینڈ", "انڈرسٹینڈ"],
    "important": ["امپورٹن", "امپورٹنٹ"],
    "accuracy": ["ایکوری سی", "ایکوریسی", "ایکیوریسی"],
    "training": ["تریننگ", "ترینڍ", "ٹریننگ", "ترینڈ"],
    "students": ["سٹوڈنٹس", "اسٹوڈنٹس"],
    "solution": ["سلوشن", "سولوشن"],
    "percent": ["پرسنٹ", "فیصد"],
    "lecture": ["لیکچر", "لیکچرز"],
    "pattern": ["پیٹرڈ", "پیٹرن", "پیٹرنز"],
    "model": ["موڈڈل", "موڈول", "موڈل", "ماڈل"],
    "topic": ["ٹاپک", "ٹاپِک"],
    "train": ["ترین", "ٹرین"],
    "data": ["ڈیڈا", "ڈیٹا", "ڈاٹا"],
    "learn": ["لرن"],
    "test": ["تیسٹ", "ٹیسٹ"],
    "L2": ["ایل ٹو", "ایل۔ٹو"],
}


def _replacement_pairs() -> list[tuple[str, str]]:
    """
    Flatten the glossary into (variant, english) pairs, longest variant first.

    Longest first matters: 'training data' must be replaced before 'data',
    otherwise the shorter match eats part of the longer phrase.
    """
    pairs = []
    for english, variants in GLOSSARY.items():
        for variant in variants:
            pairs.append((variant, english))
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    return pairs


PAIRS = _replacement_pairs()

# every known variant, used by the fuzzy pass
ALL_VARIANTS = [variant for variant, _ in PAIRS]
VARIANT_TO_ENGLISH = {variant: english for variant, english in PAIRS}


def normalize(text: str) -> tuple[str, list[dict]]:
    """
    Replace known Urdu-script spellings with the correct English term.

    Returns the corrected text and a log of what was changed, so the
    corrections can be shown to the user instead of happening silently.
    """
    if not text:
        return text, []

    corrected = text
    changes = []

    for variant, english in PAIRS:
        count = corrected.count(variant)
        if count:
            corrected = corrected.replace(variant, english)
            changes.append(
                {"found": variant, "replaced_with": english, "times": count,
                 "method": "exact"}
            )

    return corrected, changes


def fuzzy_pass(text: str, threshold: float = 0.82) -> tuple[str, list[dict]]:
    """
    Second pass for spellings the glossary has not seen yet.

    Each remaining Urdu word is compared against every known variant.
    A close match above the threshold is treated as the same term.
    Run this after normalize(), never instead of it.
    """
    if not text:
        return text, []

    changes = []
    tokens = re.findall(r"\S+", text)
    out = []

    for token in tokens:
        # skip anything already in Latin script or numeric
        if not re.search(r"[\u0600-\u06FF]", token):
            out.append(token)
            continue

        match = difflib.get_close_matches(token, ALL_VARIANTS, n=1, cutoff=threshold)
        if match:
            english = VARIANT_TO_ENGLISH[match[0]]
            out.append(english)
            changes.append(
                {"found": token, "replaced_with": english, "times": 1,
                 "method": f"fuzzy ({difflib.SequenceMatcher(None, token, match[0]).ratio():.2f})"}
            )
        else:
            out.append(token)

    return " ".join(out), changes


def clean_transcript(text: str, use_fuzzy: bool = True) -> tuple[str, list[dict]]:
    """Run both passes and return the corrected text with a combined change log."""
    corrected, changes = normalize(text)

    if use_fuzzy:
        corrected, fuzzy_changes = fuzzy_pass(corrected)
        changes = changes + fuzzy_changes

    return corrected, changes


def term_count(text: str) -> int:
    """How many known technical terms appear in a piece of text, in any form."""
    total = 0
    for variant in ALL_VARIANTS:
        total += text.count(variant)
    return total
