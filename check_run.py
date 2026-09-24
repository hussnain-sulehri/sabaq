"""
Show where one transcript's counts differ from the ground truth.

    python check_run.py db_keys__live__run1 ml_overfitting__live__run1

Prints every term counted more often than the speaker said it, every
replacement the normalizer made, and the corrected text, so a "Wrong" in the
score table can be traced to its cause.
"""

import json
import sys

from evaluate import CACHE, _matchers, count_terms
from ground_truth import LECTURES
from normalizer import clean_transcript

for name in sys.argv[1:]:
    lecture = name.split("__")[0]
    data = json.loads((CACHE / f"{name}.json").read_text(encoding="utf-8"))
    corrected, changes = clean_transcript(data["raw"], use_fuzzy=False)

    matchers = _matchers(LECTURES[lecture])
    before = count_terms(data["raw"], matchers)
    after = count_terms(corrected, matchers)
    expected = {**LECTURES[lecture]["core"], **LECTURES[lecture]["general"]}

    print(f"\n== {name}")
    for term in sorted(set(expected) | set(after)):
        e, b, a = expected.get(term, 0), before.get(term, 0), after.get(term, 0)
        if a > e:
            print(f"  OVER    {term}: said {e}, found {a} after correction "
                  f"({b} already English)")
    for c in changes:
        print(f"  change  {c['found']} -> {c['replaced_with']} x{c['times']}")
    devanagari = any("\u0900" <= ch <= "\u097F" for ch in data["raw"])
    print(f"  devanagari: {devanagari}")
    print("  corrected:")
    print("  " + corrected)
