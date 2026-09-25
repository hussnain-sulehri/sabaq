"""
Spoken answers for Sabaq.

The voice out is the browser's own speech synthesis rather than a hosted TTS
API, for three reasons that all point the same way:

- Urdu. The hosted TTS models on both providers are English first. A ur-PK
  voice ships with Windows, Android and macOS, so the browser already has
  the voice the classroom needs.
- Quota. Gemini's free tier is 20 generation requests a day. Spending any of
  them on reading a sentence aloud, when the sentence has already been
  generated, is the wrong place to spend them.
- Latency. Nothing leaves the machine, so the answer starts speaking as soon
  as it is on screen.

The cost is honest: voice availability depends on the listener's device, and
a machine with no Urdu voice falls back to the default voice reading Urdu
text, which sounds wrong. The component says which voice it used, so that
failure is visible rather than mysterious.

Hindi is deliberately not a fallback for Urdu. The answer is written in
Urdu script, which a Hindi voice cannot read, and picking one made a silent
or garbled reading look like success.

Autoplay is attempted and a button is always drawn, because most browsers
refuse to speak until the page has been clicked at least once.
"""

import json

import streamlit.components.v1 as components

# Browser language tags, in the order the voice picker should try them.
VOICE_TAGS = {
    "Urdu": ["ur-PK", "ur-IN", "ur"],
    "English": ["en-GB", "en-US", "en"],
}


def speak(text: str, language: str = "Urdu", autoplay: bool = True) -> None:
    """Draw a play control that reads `text` aloud in the listener's browser."""
    if not text:
        return

    # Escaped so a reply containing "</script>" cannot close the script
    # block it is embedded in.
    payload = json.dumps(
        {
            "text": text,
            "tags": VOICE_TAGS.get(language, VOICE_TAGS["English"]),
            "autoplay": autoplay,
        }
    ).replace("</", "<\\/")

    components.html(
        f"""
        <div style="font-family: system-ui, sans-serif; font-size: 0.85rem;">
          <button id="say" style="padding: 0.4rem 0.9rem; border-radius: 0.4rem;
            border: 1px solid #ccc; background: #f6f6f6; cursor: pointer;">
            Play answer
          </button>
          <button id="stop" style="padding: 0.4rem 0.9rem; border-radius: 0.4rem;
            border: 1px solid #ccc; background: #fff; cursor: pointer;">
            Stop
          </button>
          <span id="voice" style="margin-left: 0.6rem; color: #666;"></span>
        </div>

        <script>
        const config = {payload};
        const synth = window.speechSynthesis;

        function pickVoice() {{
          const voices = synth.getVoices();
          for (const tag of config.tags) {{
            const match = voices.find(v => v.lang && v.lang.toLowerCase()
              .startsWith(tag.toLowerCase()));
            if (match) return match;
          }}
          return null;
        }}

        function say() {{
          synth.cancel();
          const utterance = new SpeechSynthesisUtterance(config.text);
          const voice = pickVoice();
          const label = document.getElementById("voice");

          if (voice) {{
            utterance.voice = voice;
            utterance.lang = voice.lang;
            label.textContent = "voice: " + voice.name + " (" + voice.lang + ")";
          }} else {{
            utterance.lang = config.tags[0];
            label.textContent =
              "no " + config.tags[0] + " voice on this device, using the default";
          }}

          utterance.rate = 0.95;
          synth.speak(utterance);
        }}

        document.getElementById("say").onclick = say;
        document.getElementById("stop").onclick = () => synth.cancel();

        // Voices load asynchronously in Chrome, so the first pick can be
        // empty. Autoplay runs once, whichever of the two paths gets there
        // first; the voice list can change more than once while loading.
        let spoken = false;
        function autoSay() {{
          if (config.autoplay && !spoken) {{ spoken = true; say(); }}
        }}
        synth.onvoiceschanged = autoSay;
        if (synth.getVoices().length) autoSay();
        </script>
        """,
        height=60,
    )