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
- A comparison view across saved lectures, showing where the glossary holds
  and where it does not

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

**6. Model availability moves.** `gemini-2.5-flash` was retired for new users
during this build. Model choice is now automatic: available models are found
with a listing call, which costs no generation quota, and the first working one
from a preference list is used. `GEMINI_MODEL` pins one model when set.

**7. The transcript can switch writing system mid-sentence.** The database
lecture began in Urdu script and flipped to Devanagari partway through, in the
middle of a sentence, and never switched back. The same speaker, the same
recording:

> ...اب فورن کی کیا बनाती है तो वो दूसरी टेबल की प्राइमरी की को अपने अंदर रखती है

This is the single biggest limit of the glossary approach. Every variant
collected is in Urdu script, so nothing in the Devanagari half can be matched.
Handling it needs script detection before normalization, which is the next
piece of work.

**8. Acronyms survive, lowercase terms do not.** The web lecture returned
HTML, CSS, JavaScript, DOM Events, Media Query and Responsive Design correctly
in Latin script, unprompted. Acronyms are spoken letter by letter, so there is
no Urdu phonetic form to fall back on. But CSS appeared as both `CSS` and
`سی ایس ایس` in the same transcript, so the behaviour is inconsistent even
within one file.

**9. Short tags collapse.** "h1 tag" came back as `پی ای ٹیگ`, which reads as
"P A tag". The digit and the letter are both gone. Single letters and
alphanumeric tags are the worst case, worse than any full word.

## Measured across three lectures

Each lecture was scripted before recording, so the correct text was known in
advance.

| Lecture | Subject | Seconds | Words | Corrections | Unique terms | Per 100 words |
|---|---|---|---|---|---|---|
| Overfitting | AI / ML | 59 | 157 | 32 | 23 | 20.4 |
| Database | Database | 58 | 137 | 4 | 2 | 2.9 |
| Web | Web development | 51 | 137 | 3 | 2 | 2.2 |

The AI lecture scores seven times higher than the other two. Two separate
causes, and both are real limits rather than one:

- The glossary holds AI vocabulary only, so database and web terms are not in it.
- The database transcript switched to Devanagari partway through, so half of it
  was invisible to a matcher built on Urdu-script variants.

Only `students`, `data` and `lecture` appeared across subjects. Everything else
was domain specific, which is the answer to whether a hand-built glossary
generalises: within a subject yes, across subjects no.

**Result:** 32 term corrections on the AI lecture, no false positives with
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
teacher.py -> Gemini       one combined request, automatic model selection
   |
   v
summary, key points, practice questions
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
GEMINI_MODEL=
```
`GEMINI_MODEL` is optional. Leave it empty for automatic selection, or set it
to pin one model.

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
| `normalizer.py` | Glossary and the two correction passes |
| `teacher.py` | Gemini prompts, model selection, retries, note generation |
| `runs.py` | Saving runs and comparing lectures |
| `config.py` | Key and setting loading |
| `.env.example` | Template showing what to set |

## Built with
Python, Streamlit, AssemblyAI Speech-to-Text, Google Gemini API.
## Limits
The glossary covers AI and machine learning vocabulary. Database and web
lectures corrected fewer than 3 terms per 100 words against 20.4 for AI, so a
new subject needs its own term list.
It assumes one writing system. A transcript that switches to Devanagari
mid-sentence cannot be matched at all.
Every variant came from real output. The glossary grows by running more
lectures through the tool, not by guessing spellings ahead of time.
Three lectures, one speaker. Different accents will produce different
transliterations.
Gemini's free tier allows 20 generation requests per day per model. Notes use
one request. Quota errors are not retried on the same model, so a failure does
not consume the rest of the budget.
No word error rate benchmark yet. Correction counts are measured, overall
transcription accuracy is not.
## Next
Script detection before normalization, so a transcript that switches writing
system can still be corrected.
Word error rate split by pure Urdu segments versus segments containing English
terms.
A glossary that learns new variants from corrections instead of being written
by hand, and expands per subject from new lectures.
Speaker separation so student questions are marked apart from the lecturer.
Search across multiple lectures.