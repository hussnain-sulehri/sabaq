"""
Evaluation for Sabaq: precision and recall on every lecture, both paths,
repeated runs, with the glossary frozen first.

    python evaluate.py freeze
    python evaluate.py import runs/overfitting.json ml_overfitting
    python evaluate.py transcribe --audio lectures --paths batch live --runs 2
    python evaluate.py score

Audio files are found as lectures/<lecture_id>.<mp3|wav|m4a|mp4>, using the
ids in ground_truth.py. Transcripts are cached in eval_runs/, so scoring can
be re-run for free and a crashed session loses nothing.

For each transcript, per term:
    expected   times the speaker said it (ground_truth.py)
    model      already in English in the raw transcript
    glossary   brought to English by the normalizer
    missed     still not in English after correction
And across the transcript:
    inserted   every replacement the normalizer made
    wrong      replacements that did not produce an expected term
precision = glossary / inserted, recall = (model + glossary) / expected.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

from ground_truth import ALIASES, LECTURES
from normalizer import GLOSSARY, clean_transcript, unknown_terms

CACHE = Path("eval_runs")
RESULTS = Path("results")
FROZEN = RESULTS / "glossary_frozen.json"
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".mp4")

# Streaming handshakes time out now and then. Retry only those.
CONNECT_ATTEMPTS = 3
CONNECT_WAIT_SEC = 20


# -----------------------------
# Glossary freeze
# -----------------------------

def glossary_hash() -> str:
    blob = json.dumps(GLOSSARY, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def check_frozen(allow_changed: bool) -> str:
    current = glossary_hash()
    if not FROZEN.exists():
        print("WARNING: glossary not frozen. Run `python evaluate.py freeze` "
              "before transcribing held-out lectures.")
        return current
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))["hash"]
    if frozen != current and not allow_changed:
        sys.exit(
            f"Glossary changed since it was frozen ({frozen} -> {current}). "
            "Scores on held-out lectures would no longer be held out. Revert "
            "normalizer.py, or pass --allow-changed-glossary and report it."
        )
    return current


def cmd_freeze(_args) -> None:
    RESULTS.mkdir(exist_ok=True)
    FROZEN.write_text(
        json.dumps({"hash": glossary_hash(), "glossary": GLOSSARY},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Glossary frozen at {glossary_hash()}. Commit results/ now.")


# -----------------------------
# Transcripts
# -----------------------------

def cache_path(lecture: str, path: str, run: int) -> Path:
    return CACHE / f"{lecture}__{path}__run{run}.json"


def cmd_import(args) -> None:
    """Use an already saved app run as batch run 1, instead of paying again."""
    if args.lecture not in LECTURES:
        sys.exit(f"Unknown lecture id: {args.lecture}")
    saved = json.loads(Path(args.file).read_text(encoding="utf-8"))
    CACHE.mkdir(exist_ok=True)
    target = cache_path(args.lecture, args.path, 1)
    target.write_text(json.dumps({
        "raw": saved["raw"], "turns": [], "source": f"imported {args.file}",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Imported {args.file} as {target}")


def _api_key() -> str:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        sys.exit("Set ASSEMBLYAI_API_KEY in .env or the environment.")
    return key


def _batch(key: str, audio: Path) -> dict:
    import assemblyai as aai

    aai.settings.api_key = key
    config = aai.TranscriptionConfig(language_detection=True)
    result = aai.Transcriber().transcribe(str(audio), config=config)
    if result.status == aai.TranscriptStatus.error:
        raise RuntimeError(result.error)
    detected = (result.json_response or {}).get("language_code")
    return {"raw": result.text or "", "turns": [], "language": detected}


def _live(key: str, audio: Path) -> dict:
    from live import WHISPER_RT, LiveTranscriber, file_chunks

    session = LiveTranscriber(key, speech_model=WHISPER_RT)
    raw = session.run(file_chunks(audio))
    turns, _, error = session.snapshot()
    if error:
        raise RuntimeError(error)
    return {"raw": raw, "turns": turns, "lag": session.lag()}


def find_audio(folder: Path, lecture: str) -> Path | None:
    for ext in AUDIO_EXT:
        candidate = folder / f"{lecture}{ext}"
        if candidate.exists():
            return candidate
    return None


def cmd_transcribe(args) -> None:
    check_frozen(args.allow_changed_glossary)
    key = _api_key()
    CACHE.mkdir(exist_ok=True)
    folder = Path(args.audio)

    for lecture in LECTURES:
        audio = find_audio(folder, lecture)
        if not audio:
            print(f"-- {lecture}: no audio in {folder}, skipped")
            continue

        for path in args.paths:
            for run in range(1, args.runs + 1):
                target = cache_path(lecture, path, run)
                if target.exists():
                    print(f"   {target.name}: cached")
                    continue
                print(f">> {lecture} {path} run {run} ...", flush=True)
                data = None
                for attempt in range(1, CONNECT_ATTEMPTS + 1):
                    try:
                        data = _batch(key, audio) if path == "batch" else _live(key, audio)
                        break
                    except Exception as error:  # keep going; one bad file is not the run
                        # Retrying a failed connection is not re-rolling a
                        # result: no transcript was produced. A session that
                        # connected and then failed is not retried.
                        text = str(error).lower()
                        connect = "connection failed" in text or "handshake" in text
                        if connect and attempt < CONNECT_ATTEMPTS:
                            print(f"   connection failed, retrying in "
                                  f"{CONNECT_WAIT_SEC}s ({attempt}/{CONNECT_ATTEMPTS})")
                            time.sleep(CONNECT_WAIT_SEC)
                            continue
                        print(f"   FAILED: {error}")
                        break
                if data is None:
                    continue
                data["audio"] = audio.name
                target.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
                print(f"   {len(data['raw'].split())} words saved")


# -----------------------------
# Scoring
# -----------------------------

def _matchers(lecture: dict) -> list[tuple[re.Pattern, str, str]]:
    """(pattern, canonical term, tier), longest surface form first."""
    forms = []
    for tier in ("core", "general"):
        for term in lecture[tier]:
            for surface in {term, *ALIASES.get(term, [])}:
                forms.append((surface, term, tier))
    forms.sort(key=lambda f: len(f[0]), reverse=True)

    compiled = []
    for surface, term, tier in forms:
        body = r"\s+".join(re.escape(part) for part in surface.split())
        pattern = re.compile(
            rf"(?<![A-Za-z0-9]){body}(?:s|es)?(?![A-Za-z0-9])", re.IGNORECASE
        )
        compiled.append((pattern, term, tier))
    return compiled


def count_terms(text: str, matchers) -> dict[str, int]:
    """English term occurrences, each span consumed once, longest first."""
    counts: dict[str, int] = {}
    for pattern, term, _ in matchers:
        text, found = pattern.subn(" \x00 ", text)
        if found:
            counts[term] = counts.get(term, 0) + found
    return counts


def score(lecture_id: str, raw: str) -> dict:
    lecture = LECTURES[lecture_id]
    matchers = _matchers(lecture)
    corrected, changes = clean_transcript(raw, use_fuzzy=False)

    before = count_terms(raw, matchers)
    after = count_terms(corrected, matchers)

    tiers = {}
    missed_terms = []
    glossary_right = 0

    for tier in ("core", "general"):
        expected = model = glossary = 0
        for term, n in lecture[tier].items():
            m = min(before.get(term, 0), n)
            a = min(after.get(term, 0), n)
            expected += n
            model += m
            glossary += a - m
            if a < n:
                missed_terms.append({"term": term, "tier": tier, "missed": n - a})
        glossary_right += glossary
        tiers[tier] = {
            "expected": expected, "model": model, "glossary": glossary,
            "missed": expected - model - glossary,
            "recall": round((model + glossary) / expected, 3) if expected else None,
        }

    inserted = sum(c["times"] for c in changes)
    wrong = [
        c for c in changes
        if not count_terms(c["replaced_with"], matchers)
    ]

    return {
        "words": len(raw.split()),
        "core": tiers["core"],
        "general": tiers["general"],
        "inserted": inserted,
        "wrong": inserted - glossary_right,
        "precision": round(glossary_right / inserted, 3) if inserted else None,
        "unexpected_replacements": [
            {"found": c["found"], "replaced_with": c["replaced_with"],
             "times": c["times"]} for c in wrong
        ],
        "missed_terms": missed_terms,
        "uncovered": unknown_terms(corrected)[:40],
        "corrected": corrected,
    }


def _pct(x) -> str:
    return "–" if x is None else f"{x * 100:.0f}%"


def cmd_score(args) -> None:
    ghash = check_frozen(args.allow_changed_glossary)
    RESULTS.mkdir(exist_ok=True)
    rows = []

    for file in sorted(CACHE.glob("*.json")):
        lecture_id, path, run = file.stem.split("__")
        if lecture_id not in LECTURES:
            continue
        data = json.loads(file.read_text(encoding="utf-8"))
        result = score(lecture_id, data["raw"])
        meta = LECTURES[lecture_id]

        languages: dict[str, int] = {}
        for turn in data.get("turns", []):
            if turn.get("language"):
                languages[turn["language"]] = languages.get(turn["language"], 0) + 1

        rows.append({
            "lecture": lecture_id, "title": meta["title"],
            "subject": meta["subject"], "speaker": meta["speaker"],
            "split": meta["split"], "path": path, "run": int(run[3:]),
            "turns": len(data.get("turns", [])) or None,
            "languages": languages or None,
            "devanagari": bool(re.search(r"[\u0900-\u097F]", data["raw"])),
            **result,
        })

    if not rows:
        sys.exit("Nothing to score. Import or transcribe first.")

    (RESULTS / "eval_results.json").write_text(
        json.dumps({"glossary": ghash, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(rows)
    table = _markdown(rows, ghash)
    (RESULTS / "eval_table.md").write_text(table, encoding="utf-8")
    print(table)


def _write_csv(rows: list[dict]) -> None:
    fields = ["lecture", "subject", "speaker", "split", "path", "run", "words",
              "turns", "core_expected", "core_model", "core_glossary",
              "core_missed", "core_recall", "general_recall", "inserted",
              "wrong", "precision", "devanagari"]
    with open(RESULTS / "eval_results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({
                **{k: r[k] for k in ("lecture", "subject", "speaker", "split",
                                     "path", "run", "words", "turns",
                                     "inserted", "wrong", "precision",
                                     "devanagari")},
                "core_expected": r["core"]["expected"],
                "core_model": r["core"]["model"],
                "core_glossary": r["core"]["glossary"],
                "core_missed": r["core"]["missed"],
                "core_recall": r["core"]["recall"],
                "general_recall": r["general"]["recall"],
            })


def _markdown(rows: list[dict], ghash: str) -> str:
    out = [f"Glossary `{ghash}`, fuzzy matching off.\n"]

    out.append("### Per transcript\n")
    out.append("| Lecture | Speaker | Split | Path | Run | Words | Core terms "
               "| Already English | Fixed by glossary | Missed | Recall "
               "| Precision | Wrong |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (r["split"], r["lecture"], r["path"], r["run"])):
        c = r["core"]
        out.append(
            f"| {r['title']} | {r['speaker']} | {r['split']} | {r['path']} "
            f"| {r['run']} | {r['words']} | {c['expected']} | {c['model']} "
            f"| {c['glossary']} | {c['missed']} | {_pct(c['recall'])} "
            f"| {_pct(r['precision'])} | {r['wrong']} |"
        )

    out.append("\n### Pooled by split and path (core terms)\n")
    out.append("| Split | Path | Transcripts | Expected | Already English "
               "| Fixed by glossary | Recall | Glossary precision |")
    out.append("|---|---|---|---|---|---|---|---|")
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["split"], r["path"]), []).append(r)
    for (split, path), group in sorted(groups.items()):
        exp = sum(r["core"]["expected"] for r in group)
        mod = sum(r["core"]["model"] for r in group)
        glo = sum(r["core"]["glossary"] for r in group)
        ins = sum(r["inserted"] for r in group)
        right = ins - sum(r["wrong"] for r in group)
        out.append(
            f"| {split} | {path} | {len(group)} | {exp} | {_pct(mod / exp)} "
            f"| {_pct(glo / exp)} | {_pct((mod + glo) / exp)} "
            f"| {_pct(right / ins) if ins else '–'} |"
        )

    out.append("\n### Run to run (same file, same settings)\n")
    out.append("| Lecture | Path | Words per run | Turns per run | Core recall per run |")
    out.append("|---|---|---|---|---|")
    for lecture, path in sorted({(r["lecture"], r["path"]) for r in rows}):
        runs = sorted((r for r in rows if r["lecture"] == lecture and r["path"] == path),
                      key=lambda r: r["run"])
        if len(runs) < 2:
            continue
        words = " / ".join(str(r["words"]) for r in runs)
        turns = " / ".join(str(r["turns"] or "–") for r in runs)
        recall = " / ".join(_pct(r["core"]["recall"]) for r in runs)
        out.append(f"| {runs[0]['title']} | {path} | {words} | {turns} | {recall} |")

    wrong = [(r["title"], r["path"], r["run"], u)
             for r in rows for u in r["unexpected_replacements"]]
    if wrong:
        out.append("\n### Replacements that produced no expected term\n")
        out.append("Check each by hand: a real false positive, or a term missing "
                   "from ground_truth.py.\n")
        for title, path, run, u in wrong:
            out.append(f"- {title} ({path} {run}): `{u['found']}` → "
                       f"{u['replaced_with']} ×{u['times']}")

    return "\n".join(out) + "\n"


# -----------------------------
# CLI
# -----------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--allow-changed-glossary", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("freeze").set_defaults(func=cmd_freeze)

    imp = sub.add_parser("import")
    imp.add_argument("file")
    imp.add_argument("lecture")
    imp.add_argument("--path", default="batch", choices=["batch", "live"])
    imp.set_defaults(func=cmd_import)

    tr = sub.add_parser("transcribe")
    tr.add_argument("--audio", default="lectures")
    tr.add_argument("--paths", nargs="+", default=["batch", "live"],
                    choices=["batch", "live"])
    tr.add_argument("--runs", type=int, default=2)
    tr.set_defaults(func=cmd_transcribe)

    sub.add_parser("score").set_defaults(func=cmd_score)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
