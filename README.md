# Sabaq

Lecture notes for classrooms that mix Urdu and English.

Built for the AssemblyAI Voice Agent Hackathon, September 2026.

**Live app:** https://sabaq-ai.streamlit.app/
**Repo:** https://github.com/hussnain-sulehri/sabaq

---
## The problem

Teachers in Pakistan lecture in Urdu but keep technical terms in English. A
real sentence from a CS classroom sounds like this:

> "Jab hum model ko train karte hain, overfitting ho sakta hai."

Speech APIs handle each language on its own. They do not handle both in one
sentence. Running a real 60 second AI lecture through AssemblyAI produced a
transcript where every English term came back written in Urdu script, spelled
differently each time.

| Spoken term | What came back |
|---|---|
| model | موڈل, موڈڈل, موڈول |
| overfitting | آور فٹنگ, اوور فٹنگ |
| pattern | پیٹرن, پیٹرڈ |
| machine learning | مشین لرنڈنگ ("machine larnding") |
| gradient descent | گریڈینڈ ڈیسینڈز |
| learning rate | لرننگ ریڈ ("learning red") |

Roughly 30 mangled term instances in one minute of audio. A student searching
for "overfitting" finds nothing. The transcript is readable but not usable,
and a language model reading it cannot produce notes from terms it cannot
recognise.

## What Sabaq does

Upload a lecture recording. Sabaq transcribes it, restores the technical terms
to English using a glossary of spellings observed in real output, then
generates study material from the corrected text.

- Raw and corrected transcripts, side by side
- A table of every correction made, with counts
- Summary, key points, and five practice questions
- Notes in English or Urdu
- Three term styles: keep terms in English, translate them to Urdu, or write
  them in Urdu with the English term in brackets

## Findings

**1. Language detection works.** Auto detect and forced Urdu produced identical
output. The language selector was doing nothing and was removed.

**2. word_boost does not help.** AssemblyAI lets you pass expected terms to
bias the model. Passing the full technical vocabulary with `boost_param="high"`
changed one word out of roughly 30 mangled terms, and changed it for the worse.
Verified by marking the running build before drawing the conclusion. The
platform's own fix does not address this problem for Urdu.

**3. The model is not deterministic.** The same audio run twice produced
تریننگ once and ترینڍ the next time. A variant list beats a single string
match for exactly this reason.

**4. Fuzzy matching is unsafe here.** A fallback matching unknown Urdu words
against known variants by similarity corrupted تین (three) into "train",
scoring 0.86. Urdu technical transliterations sit too close to ordinary short
Urdu words. Shipped off by default, kept as a toggle so the failure stays
visible.

**5. Full Urdu translation is worse than mixing.** Translating technical terms
into Urdu produced academic vocabulary no Pakistani CS student uses
(مشین کی خودکار تعلم for machine learning, میلانِ نزول for gradient descent) and
switched to Urdu numerals, which breaks search again. The technically complete
option is the unusable one.

**6. Gemini model availability changes.** During development, Gemini model
availability changed between accounts and deployments. Instead of depending on
one fixed model name, Sabaq now automatically selects the first available Gemini
Flash model using the configured API key.

The priority order is:

1. `gemini-2.5-flash`
2. `gemini-3.6-flash`
3. `gemini-3.7-flash`
4. `gemini-2.5-flash-lite`
5. `gemini-3.5-flash`

This avoids failures caused by retired models, account-specific availability,
or different quota limits.

**Result:** 32 term corrections on a 60 second lecture, no false positives with
fuzzy matching off. Every generated claim traced back to the transcript on
manual review, with one soft embellishment ("stop before time" became "stop at
the optimal time").

## How it works

```
audio file
   |
   v
AssemblyAI speech-to-text  (language auto-detect)
   |
   v
raw transcript             English terms in Urdu script, inconsistent
   |
   v
normalizer.py              glossary of observed variants, longest match first
   |
   v
corrected transcript       terms in English, one spelling each
   |
   v
teacher.py -> Gemini       automatic model selection
                                    |
                                    v
                            summary, key points,
                            practice questions
```

The normalizer is what makes the teaching layer possible. A language model
reading the raw transcript cannot recognise آور فٹنگ as overfitting.

## Running it locally

```bash
git clone https://github.com/hussnain-sulehri/sabaq.git
cd sabaq
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # macOS and Linux
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your keys:

```
ASSEMBLYAI_API_KEY=your_assemblyai_key
GEMINI_API_KEY=your_gemini_key
```

Then:

```bash
streamlit run app.py
```

## Deploying

The app runs on Streamlit Community Cloud from this repo. Keys go in the app's
Secrets settings in TOML format, not in a file:

```toml
ASSEMBLYAI_API_KEY = "your_assemblyai_key"
GEMINI_API_KEY = "your_gemini_key"
```

No key is ever hardcoded. `config.py` reads Streamlit secrets first, then the
environment, so the same code runs in both places unchanged.

## Files

| File | What it does |
|---|---|
| `app.py` | Streamlit interface, upload, transcription, and UI workflow |
| `normalizer.py` | Glossary and technical term correction passes |
| `teacher.py` | Gemini prompts, automatic model selection, retries, and note generation |
| `config.py` | Secure API key loading |
| `.env.example` | Template showing required environment variables |

## Built with

Python, Streamlit, AssemblyAI Speech-to-Text, Google Gemini API.

## Limits

The glossary covers AI and machine learning vocabulary. A chemistry or medicine
lecture needs its own term list.

Every variant in the glossary came from real output. It grows by running more
lectures through the tool, not by guessing spellings ahead of time.

Tested on one speaker and one subject so far. Different accents will produce
different transliterations.

No word error rate benchmark yet. Correction counts are measured, overall
transcription accuracy is not.

## Next

Word error rate split by pure Urdu segments versus segments containing English
terms. Speaker separation so student questions are marked apart from the
lecturer. Search across multiple lectures. A glossary that learns new variants
from corrections instead of being hand-written. Automatic domain-specific glossary expansion from new lectures.
