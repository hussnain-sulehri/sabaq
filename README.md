# Sabaq

Lecture notes for classrooms that mix Urdu and English.

Built for the AssemblyAI Voice Agent Hackathon, September 2026.

**Live app:** https://sabaq-ai.streamlit.app/

---

## The problem

Teachers in Pakistan lecture in Urdu but keep technical terms in English. A
sentence sounds like this:

> "Jab hum model ko train karte hain, overfitting ho sakta hai."

Speech APIs handle each language on its own. They do not handle both in one
sentence. Running a real 60 second AI lecture through AssemblyAI's Urdu model
produced a transcript where every English term came back written in Urdu
script, and spelled differently each time.

From one recording:

| Spoken term | What came back |
|---|---|
| model | موڈل, موڈڈل, موڈول |
| overfitting | آور فٹنگ, اوور فٹنگ |
| pattern | پیٹرن, پیٹرڈ |
| machine learning | مشین لرنڈنگ |
| gradient descent | گریڈینڈ ڈیسینڈز |
| learning rate | لرننگ ریڈ |

A student searching for "overfitting" finds nothing. The transcript is
readable but not usable.

## What Sabaq does

Upload a lecture recording. Sabaq transcribes it, then restores the technical
terms to English using a glossary of spellings observed in real output. It
shows the raw and corrected transcripts side by side, plus a table of every
correction it made.

## Findings

**1. Language detection works.** Auto detect and forced Urdu produced
identical output on the test recording. The language selector was removed.

**2. word_boost does not help.** AssemblyAI lets you pass expected terms to
bias the model. Passing the full technical vocabulary with `boost_param="high"`
changed one word out of roughly 30 mangled terms, and changed it for the worse.
The platform's own fix does not address this problem for Urdu.

**3. The model is not deterministic.** The same audio run twice produced
تریننگ once and ترینڍ the next time. The glossary caught both, which is why a
variant list beats a single string match.

**4. Fuzzy matching is unsafe here.** A fallback that matched unknown Urdu
words against known variants by similarity corrupted تین (three) into "train",
scoring 0.86. Urdu technical transliterations are too close to ordinary short
Urdu words. The feature ships turned off and stays in the code as a toggle so
the failure is visible.

**Result:** 32 term corrections on a 60 second lecture, with no false
positives when fuzzy matching is off.

## Running it locally

```bash
git clone https://github.com/hussnain-sulehri/sabaq.git
cd sabaq
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your key:

```
ASSEMBLYAI_API_KEY=your_key_here
```

Then:

```bash
streamlit run app.py
```

## Files

| File | What it does |
|---|---|
| `app.py` | Streamlit interface, upload and transcribe |
| `normalizer.py` | Glossary and the two correction passes |
| `config.py` | Key loading from secrets or environment |
| `.env.example` | Template showing which keys are needed |

## Built with

Python, Streamlit, AssemblyAI Speech-to-Text.

## Limits

The glossary covers AI and machine learning vocabulary only. A chemistry or
medicine lecture needs its own term list.

Every variant in the glossary was observed in real output. It grows by running
more lectures through the tool, not by guessing spellings ahead of time.

Tested on one speaker so far. Different accents will produce different
transliterations.

## Next

Speaker separation so student questions are marked apart from the lecturer.
Search across multiple lectures. A word error rate benchmark split by pure
Urdu segments versus segments containing English terms.
