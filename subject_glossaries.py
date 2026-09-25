"""
Starter glossaries for six subjects beyond AI / ML.

A few core terms per subject, to show how the glossary extends to a new
subject. Every spelling below was observed in the evaluation recordings, and
each is the same word in another script: no misheard word is mapped to what
the speaker meant.

They were harvested from the same recordings they would be tested on, so
they are not part of the evaluation and no accuracy is claimed for them. A
full subject glossary needs recordings from more speakers, harvested through
the app's Not covered report, and a held-out lecture to test it on.

Kept apart from normalizer.GLOSSARY so the frozen evaluation (hash
566f8f312c1e) is unchanged.
"""

# subject -> English term -> spellings observed in batch or live output
SUBJECT_GLOSSARIES: dict[str, dict[str, list[str]]] = {
    "Database": {
        "database": ["ڈیٹا بیس", "डाटाबेस"],
        "primary key": ["پرائمری کی", "پرائیمری کی", "प्राइमरी की", "प्राइम्री की"],
        "foreign key": ["فورن کی", "फॉरेन की", "फॉरन की"],
        "table": ["ٹیبل", "टेबल"],
        "inner join": ["इन्नर ज्वाइन"],
        "left join": ["लेफ्ट ज्वाइन"],
        "normalization": ["नॉर्मलाइजेशंस"],
    },
    "Web development": {
        "web development": ["ویب ڈیولمنٹ"],
        "CSS": ["سی ایس ایس"],
        "margin": ["مارجن"],
        "padding": ["پیڈنگ", "پیڈڈنگ"],
        "layout": ["لیائوٹ"],
        "flexbox": ["فلیکس بوکس"],
        "anchor tag": ["اینکر ٹیگ"],
    },
    "Software engineering": {
        "software engineering": ["सॉफ्टवेयर इंजीनियरिंग"],
        "software development": ["सॉफ्टवेयर डेवलपमेंट"],
        "coding": ["कोडिंग"],
        "requirement": ["रिक्वायरमेंट"],
        "testing": ["ٹیسٹنگ"],
        "agile methodology": ["एजाइल मेथडोलॉजी", "اجائل میتھوڑالوجی"],
        "sprints": ["स्प्रिंट्स"],
    },
    "Cloud computing": {
        "cloud computing": ["क्लाउड कंप्यूटिंग"],
        "Docker": ["डॉकर", "डोकर"],
        "containers": ["कंटेनर्स"],
        "server": ["सर्वर"],
        "deploy": ["डिप्लोय"],
    },
    "Cybersecurity": {
        "phishing attack": ["फिशिंग अटैक", "फिशिंग एटेक"],
        "phishing": ["फिशिंग"],
        "cybersecurity": ["साइबर सिक्योरिटी", "साइबर सेक्यूरिटी"],
        "attacker": ["अटैकर"],
        "password": ["पासवर्ड"],
        "firewall": ["फायरवॉल"],
        "encryption": ["इंक्रिप्शन"],
    },
    "Operating systems": {
        "operating system": ["ऑपरेटिंग सिस्टम"],
        "process": ["प्रोसेस"],
        "processes": ["प्रोसेसेस"],
        "memory": ["मेमोरी", "मैमरी"],
        "CPU": ["सीपीयू"],
        "scheduling": ["स्केजूलिंग", "स्केजुलिंग"],
        "round robin": ["राउंड रोबिन"],
        "deadlock": ["डेडलॉक"],
    },
}

SUBJECTS = tuple(SUBJECT_GLOSSARIES)
