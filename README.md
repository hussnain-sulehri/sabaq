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

- Live streaming mode: terms are corrected turn by turn while the lecture is
  still running, with the detected language shown per turn
- Ask the lecture a question by voice and hear the answer read back, grounded
  in the transcript and labelled when the teacher did not cover it
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

**10. Two note generators, chosen for opposite reasons.** Gemini runs first
because it writes better Urdu, and Urdu notes are the mode that suffers most
from a weak model. Groq is the fallback because it is the larger floor:
Gemini's free tier allows 20 generation requests per day per model, Groq's
allows hundreds to thousands. The fallback should be the provider still
standing when the scarce one runs out, not the other way round. Either key
alone runs the app; a missing key is skipped, not an error. The sidebar can
pin one provider, which is how the two are compared on the same transcript.

A key that is set but unusable is the failure that looks like success. The
`groq` package was missing from `requirements.txt` while `GROQ_API_KEY` was
set, so the deployed app reported the key as loaded and silently never used
it. The sidebar now reports each provider separately, with the reason when
one cannot answer.

**11. Only one streaming model can hear this classroom.** The live path was
built for the voice agent brief, and the model choice was made for us.
`u3-rt-pro` and `universal-streaming-multilingual` cover English, Spanish,
German, French, Portuguese and Italian, so neither can transcribe Urdu at all.
`whisper-rt` covers 99 languages including Urdu, detects the language itself
and rejects a language parameter. It is slower than the other two. That is not
a preference, it is the only option.

The side effect is the useful part. Streaming turns carry `language_code` and
`language_confidence`, so the Urdu to Devanagari flip from finding 7 is
reported as a `hi` turn at the moment it happens, instead of being discovered
afterwards by scanning Unicode ranges. The same recording that produced
finding 7 can now say which turn the speaker switched on.

**12. A grounded answer has to say when it is not grounded.** Asked "overfitting
kya hoti hai", a model answers well whether or not the teacher covered it, and
the student cannot tell which happened. The question path returns `in_lecture`
and the piece of transcript the answer rests on, so an answer from outside the
lecture is labelled as one. This is the same discipline as the correction
table: show the working, do not just show the output.

The student's question runs through the normalizer before it reaches the
model. A question transcribed as آور فٹنگ and a transcript corrected to
overfitting are otherwise two different words, and the retrieval fails on the
term the whole app exists to fix.

**13. The voice out is the browser, not an API.** Both note providers offer
text-to-speech and both are English first, while a ur-PK voice already ships
with Windows, Android and macOS. Browser speech synthesis also costs none of
Gemini's twenty daily requests and starts speaking with no round trip. The
cost is that the voice depends on the listener's device, so the component
reports which voice it used instead of failing quietly into an English voice
reading Urdu text.

**14. The streaming model writes technical terms in English by itself, but not
reliably.** whisper-rt returned `overfitting`, `training accuracy`,
`test accuracy`, `regularization`, `dropout`, `L2`, `early stopping`,
`gradient descents`, `learning rate`, `model`, `train`, `pattern` and `learn`
in Latin script, unprompted, from Urdu speech. The batch API returned almost
none of these in English.

It is not consistent within a single transcript. The same lecture contains
`اور فٹنگ` in turn 1 and `overfitting` in turn 6, ninety seconds apart, same
speaker, same word. This is finding 8 generalised from acronyms to the whole
vocabulary, and it changes what the normalizer is for: not translating terms
the model cannot write, but making consistent a term the model writes
correctly about half the time. A word that is searchable in one sentence and
not the next is still not searchable.

**15. The glossary is specific to the model that produced it.** Every variant
collected before this point came from batch transcription. Run against
streaming output, that list corrected 13 term instances. Harvesting the
spellings the streaming run exposed, all of them from the app's own uncovered
terms report, took the same transcript to 25 with no mangled terms left. A
variant list is not a property of the language; it is a property of the model,
and swapping the model invalidates part of it.

**16. Devanagari variants match without a transliteration bridge.** The plan
was to detect the script and transliterate Devanagari into Urdu before
matching. Measuring first showed that unnecessary: Python's `\w` boundary
already covers Devanagari, so a Devanagari spelling added to the glossary is
matched by the existing pass. `ट्रेनिंग डैटा`, `मोडल`, `डाट` and `पैठरन` are
now corrected directly. The remaining gap is vocabulary, not script.

**17. Coverage is nondeterministic, not only spelling.** The same file
streamed twice, same settings, produced 6 turns and then 12. Finding 3 said
the model spells the same word differently on two runs; it also transcribes
different amounts of the same recording.

**18. Language detection can leave the language pair entirely.** Detected
languages across one AI lecture were `ur` ×9, `hi` ×2 and `fr` ×1. A Hindi
sentence was labelled French at 0.42 confidence. Confidence does flag a
confused turn, but it does not predict the script flip: one fully Devanagari
turn was reported as Hindi at 1.00 confidence. Confidence is a usable signal
for "this turn is unreliable" and not for "this turn changed script".

**19. Live lag is steady; turn length is not.** Measured per turn as the gap
between the audio position a turn covers and the moment it arrives, the lag
was 2.5 seconds on every turn produced while audio was still being sent.
Turn length varied by a factor of eight in the same session, from about 3
seconds of speech to about 25. Corrected terms therefore appear about 2.5
seconds behind the teacher, but in uneven blocks.

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


## The same lecture, batch against live

The AI lecture was run through both paths. Word counts match, so the two
transcripts cover the same speech.

| | Batch | Live (whisper-rt) |
|---|---|---|
| Words | 157 | 158 |
| Corrected by the glossary | 32 | 25 |
| Already in English from the model | ~0 | ~18 |
| Mangled terms remaining | 0 | 0 |
| Terms in Devanagari | 0 | 4 |
| Lag behind the audio | n/a | 2.5 sec |
| Languages reported | n/a | ur ×9, hi ×2, fr ×1 |

Before the streaming variants were harvested, the live column corrected 13 and
left 12 term instances unsearchable. The glossary reaching 25 on live output
is the measurable result of one harvest pass over the app's own uncovered
terms report.

## Problems hit, and what they turned out to be

Recorded because several of them were misdiagnosed first, and the correction
is the useful part.

| Symptom | Actual cause | Resolution |
|---|---|---|
| Groq key loaded, Groq never used | `groq` missing from `requirements.txt`; the `ImportError` was swallowed | Dependency added, import error kept, per-provider status shown in the sidebar |
| Live mode refused mp3 and mp4 | No system ffmpeg | `imageio-ffmpeg` ships a binary inside the venv; a system ffmpeg is used when present |
| 70% of the lecture missing from live output | The socket was closed two seconds after the audio ended, while the server still held most of the transcript | Wait until turns stop arriving, plus a collection window after terminate |
| Lag reported as 4s, then 23s, then 30s | The metric was reading the tail flushed after termination, not the live lag | Each turn records its audio position and its arrival time; live lag measured only on turns produced during audio |
| Half the words missing mid-stream | Misdiagnosis. The turns were contiguous; turn length simply varies by up to 8× | No change needed; turn length documented instead |
| `AttributeError: no attribute 'notices'` after a 90 second run | Two files updated out of step | The reader tolerates an older module rather than discarding a finished session |
| Devanagari half invisible in the uncovered terms report | The report read Urdu script only | Both scripts read and labelled |

## How it works

```
audio file                          microphone or a file played at
   |                                recording speed
   v                                   |
AssemblyAI batch STT                    v
(language auto-detect)              AssemblyAI streaming STT over WebSocket
   |                                whisper-rt, language detected per turn
   |                                   |
   |                                   v
   |                                one turn, corrected before the next
   |                                one arrives
   |                                   |
   +-----------------+-----------------+
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
   |                        falls through to Groq when Gemini is out of quota
   |
   v
summary, key points, practice questions


student question (voice)
   |
   v
streaming STT -> normalizer -> same corrected transcript as context
   |
   v
answer, labelled in_lecture or not, read aloud by the browser
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
GROQ_API_KEY=your_groq_key
GEMINI_MODEL=
GROQ_MODEL=
```
`ASSEMBLYAI_API_KEY` is required. At least one of `GEMINI_API_KEY` and
`GROQ_API_KEY` is required; setting both enables the fallback. The two model
variables are optional: leave them empty for automatic selection, or set one
to pin a model.

Then:

```bash
streamlit run app.py
```
Live mode needs two system packages that pip does not install:

- **ffmpeg**, to decode mp3, m4a and mp4 into the raw PCM the socket takes.
  `imageio-ffmpeg` in `requirements.txt` ships a binary inside the virtual
  environment, so pip alone is enough; a system ffmpeg is used when present.
  With neither, only a 16 kHz mono wav can be streamed, and the app says so.
- **libportaudio2**, for microphone capture. Without it the microphone option
  disappears and a file can still be streamed, which is how the deployed app
  runs: Streamlit Cloud has no audio device.

Both are listed in `packages.txt` for Streamlit Cloud. Locally:
`sudo apt install ffmpeg libportaudio2`, or `brew install ffmpeg portaudio`.
## Deploying

The app runs on Streamlit Community Cloud from this repo. Keys go in the app's
Secrets settings in TOML format, not in a file:
```toml
ASSEMBLYAI_API_KEY = "your_assemblyai_key"
GEMINI_API_KEY = "your_gemini_key"
GROQ_API_KEY = "your_groq_key"
```
No key is ever hardcoded. `config.py` reads Streamlit secrets first, then the
environment, so the same code runs in both places unchanged.

## Files

| File | What it does |
|---|---|
| `app.py` | Streamlit interface, upload, transcription, and UI workflow |
| `live.py` | Streaming session, audio sources, per-turn language |
| `speak.py` | Reads answers aloud through the browser's own voice |
| `normalizer.py` | Glossary and the two correction passes |
| `teacher.py` | Prompts, provider and model selection, retries, note generation |
| `runs.py` | Saving runs and comparing lectures |
| `config.py` | Key and setting loading |
| `packages.txt` | System packages for Streamlit Cloud: ffmpeg, portaudio |
| `.env.example` | Template showing what to set |

## Built with
Python, Streamlit, AssemblyAI Speech-to-Text, Google Gemini API, Groq API.
## Limits
The glossary covers AI and machine learning vocabulary. Database and web
lectures corrected fewer than 3 terms per 100 words against 20.4 for AI, so a
new subject needs its own term list.
Devanagari is covered only where a spelling has been observed and added.
There is no transliteration between scripts, so an unseen Devanagari spelling
is missed exactly as an unseen Urdu one is.
A variant list is model-specific. Changing speech model invalidates part of
the glossary, as measured in finding 15.
Every variant came from real output. The glossary grows by running more
lectures through the tool, not by guessing spellings ahead of time.
Three lectures, one speaker. Different accents will produce different
transliterations.
Gemini's free tier allows 20 generation requests per day per model. Notes use
one request. Quota errors are not retried on the same model, so a failure does
not consume the rest of the budget, and the app falls through to Groq before
giving up. Groq enforces requests and tokens per minute and per day at once
and returns 429 for all four, so its cooldown honours the retry-after header
when the API sends one.
Streaming is billed on how long the socket stays open, not on how much audio
is sent, so the microphone session is capped and every path terminates the
socket in a `finally` block.
Spoken answers depend on the listener's device having a voice for the
language. A machine with no Urdu voice reads Urdu text in an English voice.
Live mode uses `whisper-rt`, which is the slowest of the three streaming
models. The fast ones do not support Urdu, so the latency is the price of the
language.
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