"""
Ground truth for the Sabaq evaluation.

Each lecture lists the English terms the speaker actually said, and how many
times. The scorer counts them in the transcript before and after correction,
which gives every correction count a denominator.

core     subject vocabulary
general  everyday English words the speaker used (students, important...)

Counting rules, so a second person could redo them:
- Longest phrase first. "Unit Testing" is one "unit testing", not also a
  "testing". "Software Development Life Cycle" is not also "software
  development".
- A plural counts as its singular ("processes" -> process).
- The four new lectures were first counted from their scripts, then
  recounted from what the speakers actually said, because they paraphrased:
  "requirement ko gather karte hain" rather than "Requirement Gathering",
  "phishing attack" three times rather than once. The recount reads the
  batch and live transcripts together, since each drops or mishears parts
  the other keeps. Mishearings count as what was said ("variant control" is
  a missed "version control").
- The scripts for the first three were not kept, so they are counted from
  their transcripts,
  taking the fuller of batch and live. The first count used batch only and
  missed speech batch had dropped: a third "overfitting" in the AI lecture,
  and a whole sentence plus the ending of the database lecture. Both were
  corrected from the live transcripts. A term dropped by both paths is still
  not counted.

split (two questions: is the speaker new, is the subject in the glossary?)
- harvested                   the glossary was built from this recording
- same speaker · new subject  S1, but the glossary holds no terms for it
- new speaker · new subject   nothing about it was seen
- new speaker · same subject  the AI script, read by someone else

Note: the "database" entry was added from the database recording's own
transcript, so that one term is harvested there.
"""

# Latin spellings a model might produce for the same term. Plurals ending in
# s / es are matched automatically and do not need listing.
ALIASES: dict[str, list[str]] = {
    "hyper text markup language": ["hypertext markup language"],
    "flexbox": ["flex box"],
    "javascript": ["java script"],
    "color": ["colour"],
    "media query": ["media queries"],
    "h1 tag": ["h 1 tag"],
    "dependency": ["dependencies"],
    "dockerfile": ["docker file"],
    "devops": ["dev ops"],
    "github actions": ["git hub actions", "github action"],
    "ci/cd pipeline": ["ci cd pipeline", "cicd pipeline", "ci/cd"],
    "kubernetes": ["kubernetes"],
    "cybersecurity": ["cyber security"],
    "cyber attack": ["cyberattack"],
    "two-factor authentication": [
        "two factor authentication", "2fa", "two-factor", "two factor",
    ],
    "email": ["e-mail"],
    "username": ["user name"],
    "company": ["companies"],
    "requirement": ["requirements"],
    "agile methodology": ["agile methodologies", "agile"],
    "first come first serve": [
        "first come first served", "first come, first serve", "fcfs",
    ],
    "shortest job first": ["sjf"],
    "database": ["data base"],
    "normalization": ["normalisation"],
    "query optimization": ["query optimisation"],
    "index": ["indexes", "indices"],
    "regularization": ["regularisation"],
}

# Terms of the original AI lecture, reused for any re-read of the same script.
_ML_CORE = {
    "machine learning": 1, "overfitting": 3, "model": 3, "train": 2,
    "training data": 2, "data": 2, "pattern": 3, "training accuracy": 1,
    "test accuracy": 1, "regularization": 2, "dropout": 1, "l2": 1,
    "early stopping": 1, "training": 1, "gradient descent": 1,
    "learning rate": 1,
}
_ML_GENERAL = {
    "student": 1, "important": 1, "topic": 1, "lecture": 1, "solution": 1,
    "understand": 1, "learn": 1, "use": 1,
}

LECTURES: dict[str, dict] = {
    # ---------------- the three original lectures, one speaker ----------
    "ml_overfitting": {
        "title": "Overfitting", "subject": "AI / ML", "speaker": "S1",
        "split": "harvested",
        "core": _ML_CORE, "general": _ML_GENERAL,
    },
    "db_keys": {
        "title": "Primary and foreign keys", "subject": "Database",
        "speaker": "S1", "split": "same speaker · new subject",
        # Counted from the live transcript, which kept a sentence and the
        # ending that batch dropped. The live model wrote رول, so the
        # number in the example is roll number, not row number.
        "core": {
            "database": 2, "primary key": 4, "foreign key": 3, "table": 7,
            "column": 1, "row": 3, "unique": 1, "relation": 1, "data": 2,
            "join": 1, "inner join": 1, "left join": 1, "normalization": 1,
            "duplicate": 1, "first normal form": 1, "second normal form": 1,
            "third normal form": 1, "index": 1,
            # Spoken "query optimization"; the live model heard "security"
            # (سیکیورٹی), a substitution no glossary can undo.
            "query optimization": 1,
        },
        "general": {
            "student": 3, "concept": 1, "same": 1, "use": 1, "level": 1,
            "lecture": 1,
        },
    },
    "web_html_css": {
        "title": "HTML and CSS", "subject": "Web development",
        "speaker": "S1", "split": "same speaker · new subject",
        "core": {
            "web development": 1, "html": 2, "css": 3,
            "hyper text markup language": 1, "page": 2, "structure": 1,
            "heading": 1, "h1 tag": 1, "link": 1, "anchor tag": 1,
            "styling": 1, "color": 1, "font": 1, "size": 1, "margin": 2,
            "padding": 2, "element": 1, "layout": 1, "flexbox": 1,
            "grid": 1, "float": 1, "responsive design": 1, "media query": 1,
            "javascript": 1, "dom event": 1,
        },
        "general": {
            "student": 2, "lecture": 1, "control": 1, "confuse": 1,
            "time": 1, "mobile": 1, "space": 2,
        },
    },

    # ---------------- four new lectures, three new speakers -------------
    # S2: operating systems. S3: SDLC. S4: cybersecurity and cloud.
    "se_sdlc": {
        "title": "SDLC", "subject": "Software engineering",
        "speaker": "S3", "split": "new speaker · new subject",
        "core": {
            # Spoken "requirement ko gather karte hain", and "teen phase
            # hote hain" in place of the script's second "Testing".
            "software engineering": 1, "software development life cycle": 1,
            "sdlc": 1, "software development": 1, "requirement": 1,
            "system design": 1, "unit testing": 1, "integration testing": 1,
            "system testing": 1, "testing": 1, "coding": 2, "developer": 3,
            "development": 1, "software": 2, "bug": 1, "test": 1,
            "agile methodology": 1, "sprint": 1, "git": 1, "branch": 1,
            "pull request": 1, "version control": 1,
        },
        "general": {
            "client": 1, "system": 1, "fix": 1, "application": 1,
            "project": 1, "code": 1, "phase": 1, "use": 1,
        },
    },
    "cloud_docker": {
        "title": "Docker and containers", "subject": "Cloud computing",
        "speaker": "S4", "split": "new speaker · new subject",
        "core": {
            "cloud computing": 1, "docker": 5, "container": 5,
            "docker image": 1, "dockerfile": 1, "image": 1, "dependency": 1,
            "software development": 1, "deploy": 1, "deployment": 1,
            "server": 1, "scale": 1, "cloud": 1, "devops": 1,
            "kubernetes": 1, "ci/cd pipeline": 1, "github actions": 1,
            "monitoring": 1,
        },
        "general": {
            "application": 3, "developer": 2, "computer": 1, "code": 1,
            "error": 1,
        },
    },
    "sec_phishing": {
        "title": "Phishing attack", "subject": "Cybersecurity",
        "speaker": "S4", "split": "new speaker · new subject",
        # The speaker opened with "Phishing attack kya hota hai? Acha,
        # phishing attack basically cyber security ki ek term hai", added a
        # sentence on fake email, said "attacker se bachne" where the script
        # has "attacks se", and did not say "cyber attack".
        "core": {
            "cybersecurity": 3, "phishing attack": 3, "phishing": 1,
            "attack": 1, "attacker": 3,
            "sensitive information": 1, "email": 2, "account": 1,
            "password": 2, "link": 1, "username": 1, "url": 1,
            "attachment": 1, "two-factor authentication": 1, "firewall": 1,
            "encryption": 1, "intrusion detection system": 1, "security": 1,
        },
        "general": {
            "user": 2, "important": 1, "technology": 1, "click": 1,
            "download": 1, "fake": 2, "verify": 1, "company": 2,
            "identity": 1, "data": 1,
        },
    },
    "os_process": {
        "title": "Process management", "subject": "Operating systems",
        "speaker": "S2", "split": "new speaker · new subject",
        "core": {
            "operating system": 3, "process management": 1, "process": 8,
            "program": 2, "cpu scheduling": 1, "cpu": 1, "scheduling": 1,
            "algorithm": 1, "first come first serve": 1,
            "shortest job first": 1, "round robin": 2, "time quantum": 1,
            "execution time": 1, "memory": 1, "queue": 1, "deadlock": 2,
            "resource": 1,
        },
        "general": {"computer": 1, "important": 1, "concept": 1},
    },

    # ---------------- optional: the AI script read by a new speaker ------
    # The only test that holds the subject fixed and changes the voice.
    # Skipped automatically when no audio file exists for it. Set the
    # speaker to whoever records it.
    "ml_overfitting_reread": {
        "title": "Overfitting (re-read)", "subject": "AI / ML",
        "speaker": "S2", "split": "new speaker · same subject",
        "core": _ML_CORE, "general": _ML_GENERAL,
    },
}