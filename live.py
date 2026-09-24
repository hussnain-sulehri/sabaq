"""
Live streaming transcription for Sabaq.

The batch path sends a finished file and waits. This path opens a WebSocket
and corrects each turn as it lands, which is what a classroom actually needs:
the term is on screen in English while the teacher is still talking about it.

Model choice is forced by the language, not by preference:

- u3-rt-pro and universal-streaming-multilingual cover English, Spanish,
  German, French, Portuguese and Italian. Neither can hear an Urdu classroom.
- whisper-rt covers 99 languages including Urdu, detects the language itself,
  and rejects a language parameter. It is the only streaming model that works
  here, and it is slower than the other two. That trade is not optional.

Turn events carry language_code and language_confidence per turn, which is
the part that matters beyond eligibility. The Devanagari flip in the database
lecture stops being something we detect by scanning Unicode ranges after the
fact: the API labels the turn 'hi' instead of 'ur' at the moment it happens.

Billing note: streaming is billed on how long the socket stays open, not on
how much audio is sent. An abandoned session auto-closes after three hours
and bills for all three. Every path here terminates in a finally block.

Threading note: the SDK dispatches events on its own read thread, and
Streamlit widgets cannot be touched from there. Handlers only append to a
lock-protected list. The main thread reads that list between audio chunks
and draws. Nothing in this module imports Streamlit.
"""

from __future__ import annotations

import logging
import queue
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Iterator

from assemblyai.streaming.v3 import (
    BeginEvent,
    WarningEvent,
    StreamingClient,
    StreamingClientOptions,
    StreamingError,
    StreamingEvents,
    StreamingParameters,
    TerminationEvent,
    TurnEvent,
)

log = logging.getLogger("sabaq.live")

SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2
CHUNK_MS = 100
CHUNK_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE * CHUNK_MS // 1000

# The only streaming model that supports Urdu.
WHISPER_RT = "whisper-rt"

# How long to keep listening after the audio stops.
#
# Measured, not guessed: whisper-rt runs behind real time, and the lag grows
# across a session. On a 59 second lecture the socket was closed two seconds
# after the audio ended and 70% of the transcript was still queued on the
# server. Waiting a fixed number of seconds cannot be right when the backlog
# depends on the length of the recording, so the wait ends when turns stop
# arriving instead.
DRAIN_IDLE_SEC = 15.0
DRAIN_MAX_SEC = 180.0

# The server flushes whatever it still holds when the session is terminated.
# Hanging up the moment Terminate is sent throws that flush away, which is
# one candidate explanation for a missing tail.
FLUSH_SEC = 6.0


# -----------------------------
# Audio sources
# -----------------------------

def ffmpeg_path() -> str | None:
    """
    Where ffmpeg is, if it is anywhere.

    A system install is preferred. Failing that, imageio-ffmpeg ships a
    binary inside the virtual environment, which is the version that needs
    no admin rights and no PATH change: pip install imageio-ffmpeg.
    """
    found = shutil.which("ffmpeg")
    if found:
        return found

    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def ffmpeg_available() -> bool:
    return ffmpeg_path() is not None


def microphone_available() -> tuple[bool, str]:
    """
    Whether a microphone can be opened here, and why not when it cannot.

    Streamlit Cloud has no audio device, so the deployed app can only stream
    a file. Saying which of the two is missing saves a support round trip.
    """
    try:
        import sounddevice  # noqa: F401
    except Exception as error:
        return False, f"sounddevice is not usable here ({error})"

    try:
        import sounddevice as sd

        if not any(d["max_input_channels"] > 0 for d in sd.query_devices()):
            return False, "no input device found"
    except Exception as error:
        return False, f"no input device found ({error})"

    return True, ""


def _pace(chunks: Iterator[bytes], speed: float = 1.0) -> Iterator[bytes]:
    """
    Hand chunks over at the speed the audio was recorded at.

    A file read at disk speed arrives as a single burst, and turn detection
    on a burst is meaningless. Pacing makes a saved lecture behave like a
    live one, which is how the streaming path can be demonstrated on a
    machine with no microphone.

    speed above 1 sends faster than real time. A recorded question does not
    need to be replayed at talking speed, but it still needs to arrive in
    chunks rather than all at once.
    """
    deadline = time.monotonic()

    for chunk in chunks:
        yield chunk
        deadline += (CHUNK_MS / 1000) / speed
        delay = deadline - time.monotonic()
        if delay > 0:
            time.sleep(delay)


def _wav_chunks(path: Path) -> Iterator[bytes]:
    """Read a wav that is already 16 kHz mono 16-bit, without ffmpeg."""
    with wave.open(str(path), "rb") as handle:
        if (
            handle.getnchannels() != 1
            or handle.getsampwidth() != BYTES_PER_SAMPLE
            or handle.getframerate() != SAMPLE_RATE
        ):
            raise RuntimeError(
                "This wav is not 16 kHz mono 16-bit, so it needs ffmpeg to "
                "be converted. Install ffmpeg, or upload a different file."
            )

        while True:
            frames = handle.readframes(CHUNK_BYTES // BYTES_PER_SAMPLE)
            if not frames:
                return
            yield frames


def file_chunks(
    path: str | Path, realtime: bool = True, speed: float = 1.0
) -> Iterator[bytes]:
    """
    Decode any audio file to the raw PCM the socket expects.

    ffmpeg does the decoding because mp3, m4a and mp4 all arrive here and
    none of them are PCM. Without ffmpeg only an already-correct wav can be
    streamed, and that is said plainly rather than failing deep in the API.
    """
    path = Path(path)
    binary = ffmpeg_path()

    if not binary:
        chunks = _wav_chunks(path)
        return _pace(chunks, speed) if realtime else chunks

    process = subprocess.Popen(
        [
            binary, "-nostdin", "-loglevel", "error",
            "-i", str(path),
            "-f", "s16le", "-acodec", "pcm_s16le",
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def reader() -> Iterator[bytes]:
        try:
            while True:
                chunk = process.stdout.read(CHUNK_BYTES)
                if not chunk:
                    break
                yield chunk
        finally:
            # When the socket fails, reading stops before the file ends.
            # Stop ffmpeg as well, instead of letting it fail on a closed
            # pipe and report that as an ffmpeg error.
            try:
                stopped_early = process.poll() is None
                if stopped_early:
                    process.kill()
                process.stdout.close()
                code = process.wait()
                if code != 0 and not stopped_early:
                    log.error("ffmpeg failed: %s", process.stderr.read()[:400])
                process.stderr.close()
            except OSError:
                # On Windows the handle can already be released when the
                # generator is cleaned up late. Nothing is left to close.
                pass

    chunks = reader()
    return _pace(chunks, speed) if realtime else chunks


def microphone_chunks(max_seconds: float) -> Iterator[bytes]:
    """
    Microphone audio, already at the rate the socket wants.

    Capped rather than open ended. A forgotten open socket is billed for the
    full three hours before it closes itself.
    """
    import sounddevice as sd

    buffer: queue.Queue[bytes] = queue.Queue()

    def on_audio(indata, _frames, _time, status):
        if status:
            log.warning("microphone status: %s", status)
        buffer.put(bytes(indata))

    stream = sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=CHUNK_BYTES // BYTES_PER_SAMPLE,
        dtype="int16",
        channels=1,
        callback=on_audio,
    )

    stop_at = time.monotonic() + max_seconds

    with stream:
        while time.monotonic() < stop_at:
            try:
                yield buffer.get(timeout=0.5)
            except queue.Empty:
                continue


# -----------------------------
# The session
# -----------------------------

class LiveTranscriber:
    """
    One streaming session.

    Turns are collected here, not rendered here. The caller decides what a
    turn looks like on screen, which keeps this module free of Streamlit and
    testable from a plain script.
    """

    def __init__(
        self,
        api_key: str,
        speech_model: str = WHISPER_RT,
        language_detection: bool = True,
    ):
        self.api_key = api_key
        self.speech_model = speech_model
        self.language_detection = language_detection

        self._lock = threading.Lock()
        self._turns: list[dict] = []
        self._partial = ""
        self._error: str | None = None
        self._session_id: str | None = None
        self._client: StreamingClient | None = None

        # Wall clock, for measuring how far behind the audio each turn is.
        self._started = time.monotonic()
        self._last_turn_at: float | None = None
        self._audio_sent = 0.0
        self.phase = "idle"

        # Anything the server says about the session. Warnings were being
        # discarded, which is the wrong thing to discard while half the
        # transcript is going missing.
        self.notices: list[str] = []

    # --- event handlers, all on the SDK's read thread ---

    def _on_begin(self, _client, event: BeginEvent) -> None:
        with self._lock:
            self._session_id = str(getattr(event, "id", "") or "")

    def _on_turn(self, _client, event: TurnEvent) -> None:
        with self._lock:
            if not event.end_of_turn:
                self._partial = event.transcript or ""
                return

            if not (event.transcript or "").strip():
                self._partial = ""
                return

            now = time.monotonic()
            self._last_turn_at = now

            self._turns.append({
                "order": event.turn_order,
                "text": event.transcript.strip(),
                "language": event.language_code,
                "confidence": event.language_confidence,
                # Seconds between the start of the session and this turn
                # landing, against the seconds of audio sent by then. The
                # difference is the lag, and it is the number that decides
                # how long to wait at the end.
                "received_at": round(now - self._started, 1),
                "audio_at": round(self._audio_sent, 1),
            })
            self._partial = ""

    def _on_warning(self, _client, event: WarningEvent) -> None:
        code = getattr(event, "warning_code", "") or ""
        message = str(getattr(event, "warning", "") or event)
        if code:
            message = f"[{code}] {message}"
        log.warning("streaming warning: %s", message)
        with self._lock:
            self.notices.append(message)

    def _on_error(self, _client, error: StreamingError) -> None:
        log.error("streaming error: %s", error)
        with self._lock:
            self._error = str(error)

    def _on_termination(self, _client, event: TerminationEvent) -> None:
        log.info(
            "session closed after %ss of audio",
            getattr(event, "audio_duration_seconds", "?"),
        )

    # --- what the caller sees ---

    def snapshot(self) -> tuple[list[dict], str, str | None]:
        """Finished turns, the partial turn in progress, and any error."""
        with self._lock:
            return list(self._turns), self._partial, self._error

    def transcript(self) -> str:
        with self._lock:
            return " ".join(turn["text"] for turn in self._turns)

    def lag(self) -> float:
        """Seconds the last turn trailed the audio it came from."""
        with self._lock:
            if not self._turns:
                return 0.0
            last = self._turns[-1]
            return round(last["received_at"] - last["audio_at"], 1)

    def languages(self) -> dict[str, int]:
        """Turns per detected language. Empty when detection is off."""
        counts: dict[str, int] = {}
        with self._lock:
            for turn in self._turns:
                if turn["language"]:
                    counts[turn["language"]] = counts.get(turn["language"], 0) + 1
        return counts

    # --- running it ---

    def run(
        self,
        chunks: Iterator[bytes],
        on_update: Callable[[], None] | None = None,
    ) -> str:
        """
        Feed audio until it runs out, calling on_update between chunks.

        Blocking, and deliberately run on the caller's thread: on_update is
        where the UI redraws, and the UI cannot be redrawn from the SDK's
        read thread. Returns the full transcript.
        """
        parameters = StreamingParameters(
            sample_rate=SAMPLE_RATE,
            encoding="pcm_s16le",
            speech_model=self.speech_model,
            # whisper-rt detects the language itself and rejects a language
            # parameter, so language_code is never set here.
            language_detection=self.language_detection,
        )

        self._started = time.monotonic()
        self._audio_sent = 0.0
        self.phase = "listening"

        client = StreamingClient(
            StreamingClientOptions(api_key=self.api_key)
        )
        self._client = client

        # Handlers before connect: a handshake rejection is dispatched as an
        # Error event rather than raised, and an unregistered handler at that
        # moment means a failed connection looks like silence.
        client.on(StreamingEvents.Begin, self._on_begin)
        client.on(StreamingEvents.Turn, self._on_turn)
        client.on(StreamingEvents.Warning, self._on_warning)
        client.on(StreamingEvents.Error, self._on_error)
        client.on(StreamingEvents.Termination, self._on_termination)

        try:
            client.connect(parameters)

            for chunk in chunks:
                if self._error:
                    break
                client.stream(chunk)
                self._audio_sent += len(chunk) / (SAMPLE_RATE * BYTES_PER_SAMPLE)
                if on_update:
                    on_update()

            # Most of the lecture is still queued on the server when the
            # audio ends. Wait for turns to stop arriving rather than for a
            # fixed number of seconds, with a cap so a dead session cannot
            # hold the socket open and bill for it.
            self.phase = "draining"
            drain_started = time.monotonic()

            while not self._error:
                time.sleep(0.25)
                if on_update:
                    on_update()

                now = time.monotonic()
                since_turn = now - (self._last_turn_at or drain_started)

                if since_turn >= DRAIN_IDLE_SEC:
                    break
                if now - drain_started >= DRAIN_MAX_SEC:
                    log.warning("drain hit the %ss cap", DRAIN_MAX_SEC)
                    break

        finally:
            # Always terminate. An open socket bills until it times out.
            try:
                self.phase = "flushing"
                client.disconnect(terminate=True)

                # Terminate makes the server hand back what it still holds.
                # Collect it before the object goes away.
                flush_until = time.monotonic() + FLUSH_SEC
                while time.monotonic() < flush_until:
                    time.sleep(0.25)
                    if on_update:
                        on_update()
            except Exception:
                log.exception("disconnect failed")
            self._client = None
            self.phase = "done"

        if on_update:
            on_update()

        return self.transcript()


# Questions are short and already recorded, so they are sent faster than
# talking speed. Still chunked: the server needs turn boundaries, and a
# single burst does not give it any.
QUESTION_SPEED = 4.0


def transcribe_recording(api_key: str, path: str | Path) -> tuple[str, list[dict]]:
    """
    Push a short recording through the streaming socket and return its text.

    Used for student questions. The same socket as the lecture path, so a
    question is transcribed by the same model that heard the lecture, with
    the same language detection, and no second API surface to configure.
    """
    session = LiveTranscriber(api_key)
    text = session.run(file_chunks(path, realtime=True, speed=QUESTION_SPEED))
    turns, _, error = session.snapshot()

    if error:
        raise RuntimeError(error)

    return text, turns