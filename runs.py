"""
Saved runs for Sabaq.

Each transcription is stored as one JSON file so results from different
lectures can be compared side by side. This is what turns three separate
tests into evidence.

Local disk only. Streamlit Cloud wipes the filesystem on reboot, so the
deployed app keeps runs for the session and offers a CSV download instead
of pretending they persist.
"""

import csv
import io
import json
import re
import time
from pathlib import Path

RUNS_DIR = Path("runs")


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
    return cleaned.strip("-")[:40] or "lecture"


def build_run(
    label: str,
    subject: str,
    raw: str,
    corrected: str,
    changes: list[dict],
    duration: int | None,
    unknown: list[dict] | None = None,
) -> dict:
    """
    Assemble one run record, with the comparison numbers already computed.

    'unknown' is the leftover Urdu vocabulary the glossary did not cover.
    Saving it is what lets the next version of the glossary be written from
    measurements instead of from memory.
    """
    words = len(raw.split())
    total = sum(c["times"] for c in changes)
    unique_terms = sorted({c["replaced_with"] for c in changes})

    return {
        "label": label,
        "subject": subject,
        "saved_at": time.strftime("%Y-%m-%d %H:%M"),
        "duration_sec": duration,
        "words": words,
        "corrections": total,
        "unique_terms": len(unique_terms),
        "per_100_words": round(total / words * 100, 1) if words else 0.0,
        "terms": unique_terms,
        "changes": changes,
        "unknown": unknown or [],
        "raw": raw,
        "corrected": corrected,
    }


def save_run(run: dict) -> Path | None:
    """Write a run to disk. Returns None when the filesystem is not writable."""
    try:
        RUNS_DIR.mkdir(exist_ok=True)
        path = RUNS_DIR / f"{_slug(run['label'])}.json"
        path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except OSError:
        return None


def load_runs() -> list[dict]:
    """Read every saved run from disk, newest first. Missing folder is fine."""
    if not RUNS_DIR.exists():
        return []

    runs = []
    for path in RUNS_DIR.glob("*.json"):
        try:
            runs.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue

    return sorted(runs, key=lambda r: r.get("saved_at", ""), reverse=True)


def summary_rows(runs: list[dict]) -> list[dict]:
    """One row per run, for the comparison table."""
    return [
        {
            "Lecture": r["label"],
            "Subject": r["subject"],
            "Seconds": r.get("duration_sec") or "",
            "Words": r["words"],
            "Corrections": r["corrections"],
            "Unique terms": r["unique_terms"],
            "Per 100 words": r["per_100_words"],
        }
        for r in runs
    ]


def term_matrix(runs: list[dict]) -> list[dict]:
    """
    Which corrected term appeared in which lecture.

    Shows whether the glossary generalises beyond the lecture it was built
    from, or only covers the original vocabulary.
    """
    all_terms = sorted({t for r in runs for t in r["terms"]})
    rows = []

    for term in all_terms:
        row = {"Term": term}
        for r in runs:
            row[r["label"]] = "yes" if term in r["terms"] else ""
        rows.append(row)

    return rows


def to_csv(runs: list[dict]) -> str:
    """
    Comparison table as CSV text, for the deck and the write-up.

    Written with the csv module rather than string formatting, so a lecture
    name containing a comma or a quote does not break the file.
    """
    rows = summary_rows(runs)
    if not rows:
        return ""

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

    return buffer.getvalue()