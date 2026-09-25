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

Lag note: a turn's lag is the wall-clock time between the moment the audio
of its last word was sent and the moment the turn arrived, read from the
word timestamps on the turn. An earlier version subtracted "audio sent so
far" from "time since the session opened". While audio is paced at real
time those two differ by the connection time and nothing else, so it
reported a constant: the same to 0.1 s on every turn of a run, anywhere from
0.7 s to 4.1 s between runs. When a turn carries no word timestamps its lag
is recorded as unknown rather than estimated.

Threading note: the SDK dispatches events on its own read thread, and
Streamlit widgets cannot be touched from there. Handlers only append to a
lock-protected list. The main thread reads that list between audio chunks
and draws. Nothing in this module imports Streamlit.
"""

from __future__ import annotations

import bisect
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

# How long to keep listening after the audio stops, before sending Terminate.
#
# History: an early build closed the socket two seconds after the audio ended
# without terminating, and lost 70% of a 59 second lecture. The fix waited
# for 15 seconds of silence, then terminated.
#
# Measured afterwards, across the 20 evaluation runs with more than one turn:
# no turn ever arrived during those 15 seconds except the last one, and in 13
# of the 20 the last turn arrived only once Terminate had been sent. Between
# 4% and 20% of the words arrive after the audio ends, always as that one
# final turn. The backlog is one unfinished turn, and Terminate is what
# releases it. A short pause is kept so a turn already on its way is not
# raced; the cap stops a dead session holding the socket open.
DRAIN_IDLE_SEC = 3.0
DRAIN_MAX_SEC = 60.0

# Handshake time allowed per attempt. assemblyai 1.6.1 defaults to 1 second
# (with two retries), and TCP, TLS and the WebSocket upgrade are several
# round trips from Pakistan to the streaming host. The development network
# saw 8 of 36 attempts time out at the handshake. The short default is the
# likely cause, not a confirmed one: re-measure after this change.
CONNECT_TIMEOUT_SEC = 10.0

# How long disconnect(terminate=True) waits for the server's Termination
# event, which carries the audio duration the server received. assemblyai
# 1.6.1 defaults to 5 seconds. On the phishing lecture the event did not
# arrive in 2 of 2 sessions while the other six lectures received it, which
# fits a wait that is too short, though that is not confirmed. The SDK stops
# its read thread when this runs out, so nothing can be collected afterwards:
# the wait has to happen inside disconnect(), not after it.
TERMINATE_TIMEOUT_SEC = 10.0

# Audio the server reports receiving may trail audio sent by a fraction of a
# second. More than this is reported as audio the server never heard.
UNHEARD_TOLERANCE_SEC = 2.0


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

    try:
        for chunk in chunks:
            yield chunk
            deadline += (CHUNK_MS / 1000) / speed
            delay = deadline - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    finally:
        # Closing this generator must close the source too, so an ffmpeg
        # process behind it stops now rather than whenever the garbage
        # collector gets to it.
        close = getattr(chunks, "close", None)
        if close:
            close()


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

    command = [
        binary, "-nostdin", "-loglevel", "error",
        "-i", str(path),
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ac", "1", "-ar", str(SAMPLE_RATE), "-",
    ]

    def reader() -> Iterator[bytes]:
        # ffmpeg starts on the first read, not when this function is called.
        # A source that is never read, because the connection failed first,
        # then has no process behind it to hold the file open. On Windows an
        # open file cannot be deleted, and the app deletes it right after.
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        try:
            while True:
                chunk = process.stdout.read(CHUNK_BYTES)
                if not chunk:
                    break
                yield chunk
        finally:
            _stop_ffmpeg(process)

    chunks = reader()
    return _pace(chunks, speed) if realtime else chunks


def _stop_ffmpeg(process: subprocess.Popen) -> None:
    """
    Stop ffmpeg and wait until it has exited.

    Each step runs even when the one before it fails. The earlier version
    closed the pipe before waiting, and on Windows closing the pipe can
    raise; the wait was then skipped, ffmpeg was still exiting when the app
    deleted the file, and the delete failed with WinError 32.
    """
    stopped_early = process.poll() is None
    if stopped_early:
        # The socket failed or the stream ended before the file did. Stop
        # ffmpeg instead of letting it fail on a closed pipe and report that
        # as an ffmpeg error.
        try:
            process.kill()
        except OSError:
            pass

    try:
        code = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        log.error("ffmpeg did not exit within 10s")
        code = None

    for pipe in (process.stdout, process.stderr):
        try:
            if code not in (0, None) and not stopped_early and pipe is process.stderr:
                log.error("ffmpeg failed: %s", pipe.read()[:400])
            pipe.close()
        except (OSError, ValueError):
            pass


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

def _client_options(api_key: str) -> StreamingClientOptions:
    """
    Client options with the handshake and termination timeouts raised.

    Older SDKs do not have these fields, and some accept unknown fields
    silently, so the values that actually took effect are checked. Either
    way the session still works, on the SDK's own defaults.
    """
    try:
        options = StreamingClientOptions(
            api_key=api_key,
            connect_timeout=CONNECT_TIMEOUT_SEC,
            terminate_timeout=TERMINATE_TIMEOUT_SEC,
        )
    except (TypeError, ValueError):
        options = StreamingClientOptions(api_key=api_key)

    if (getattr(options, "connect_timeout", None) != CONNECT_TIMEOUT_SEC
            or getattr(options, "terminate_timeout", None) != TERMINATE_TIMEOUT_SEC):
        log.warning(
            "this assemblyai version ignores the timeout settings; handshakes "
            "and termination use its defaults. Upgrade: pip install -U assemblyai"
        )
    return options


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

        # One checkpoint per chunk: seconds of audio sent so far, and the
        # session time at which that total was reached. A turn's lag is read
        # off these, so it is measured against when its audio actually left,
        # whatever speed the audio was sent at.
        self._sent_audio: list[float] = []
        self._sent_clock: list[float] = []

        # Set when the server confirms the session is over. It reports how
        # much audio it received, which is how a stream that silently stopped
        # being heard can be told apart from one that heard everything.
        self._terminated = threading.Event()
        self.server_audio_sec: float | None = None

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
            received_at = now - self._started

            audio_end = self._turn_audio_end(event)
            sent_at = self._clock_when_sent(audio_end)
            lag = (
                round(received_at - sent_at, 1) if sent_at is not None else None
            )

            self._turns.append({
                "order": event.turn_order,
                "text": event.transcript.strip(),
                # getattr: older SDKs have no per-turn language fields, and an
                # AttributeError here would be raised on the read thread,
                # where nothing reports it.
                "language": getattr(event, "language_code", None),
                "confidence": getattr(event, "language_confidence", None),
                # Session time when the turn landed, and audio sent by then.
                # Kept for comparison with earlier runs. Their difference is
                # the connection time, not the lag (see the module notes).
                "received_at": round(received_at, 1),
                "audio_at": round(self._audio_sent, 1),
                # Where the turn's last word ends in the audio, and how long
                # after that audio was sent the turn arrived. None when the
                # turn carries no usable word timestamps.
                "audio_end": round(audio_end, 1) if audio_end is not None else None,
                "lag": lag,
                # False for the final turn released after the audio ended,
                # which waits for the drain and would skew a short session.
                "during_audio": self.phase == "listening",
            })
            self._partial = ""

    def _turn_audio_end(self, event: TurnEvent) -> float | None:
        """
        Seconds into the stream where the turn's last word ends.

        Word times are milliseconds from the start of the stream. A time past
        the audio sent so far cannot be that, so it is discarded rather than
        turned into a negative lag.
        """
        words = getattr(event, "words", None) or []
        if not words:
            return None

        end_ms = getattr(words[-1], "end", None)
        if not isinstance(end_ms, (int, float)) or end_ms <= 0:
            return None

        end = end_ms / 1000
        if end > self._audio_sent + 0.5:
            return None
        return end

    def _clock_when_sent(self, audio_sec: float | None) -> float | None:
        """Session time at which the audio up to audio_sec had been sent."""
        if audio_sec is None or not self._sent_audio:
            return None
        index = bisect.bisect_left(self._sent_audio, audio_sec)
        if index >= len(self._sent_clock):
            # Summing 0.1 s chunks drifts by a few ulps, and a word may end
            # inside the chunk still being sent. Anything within the
            # tolerance _turn_audio_end allows belongs to the last chunk.
            if audio_sec - self._sent_audio[-1] > 0.5:
                return None
            index = len(self._sent_clock) - 1
        return self._sent_clock[index]

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
        duration = getattr(event, "audio_duration_seconds", None)
        log.info("session closed after %ss of audio", duration)
        with self._lock:
            if isinstance(duration, (int, float)):
                self.server_audio_sec = float(duration)
        self._terminated.set()

    # --- what the caller sees ---

    def snapshot(self) -> tuple[list[dict], str, str | None]:
        """Finished turns, the partial turn in progress, and any error."""
        with self._lock:
            return list(self._turns), self._partial, self._error

    def transcript(self) -> str:
        with self._lock:
            return " ".join(turn["text"] for turn in self._turns)

    def lags(self, during_audio_only: bool = True) -> list[float]:
        """
        Per-turn lag in seconds, for turns that carried word timestamps.

        By default only turns produced while audio was playing, the measure
        the README reports: the final turn waits for the end of the session,
        which is a different delay. Falls back to every turn when none were
        produced during the audio, as in a very short question.
        """
        with self._lock:
            timed = [t for t in self._turns if t.get("lag") is not None]
        if during_audio_only:
            during = [t for t in timed if t.get("during_audio", True)]
            if during:
                timed = during
        return [t["lag"] for t in timed]

    def lag(self) -> float | None:
        """
        Median seconds between a turn's last word being sent and the turn
        arriving, over turns produced while audio played. None when no turn
        carried word timestamps, which is reported as unknown, not as zero.
        """
        values = sorted(self.lags())
        if not values:
            return None
        middle = len(values) // 2
        if len(values) % 2:
            return values[middle]
        return round((values[middle - 1] + values[middle]) / 2, 1)

    def audio_sent(self) -> float:
        """Seconds of audio sent to the server."""
        return round(self._audio_sent, 1)

    def unheard_audio_sec(self) -> float | None:
        """
        Seconds of audio sent that the server says it never received.

        None when the server did not report a duration. Zero when the two
        agree within tolerance. A positive number means the stream stopped
        being heard before the audio ended, which is one way a transcript
        ends early with no error.
        """
        with self._lock:
            server = self.server_audio_sec
        if server is None:
            return None
        gap = self._audio_sent - server
        return round(gap, 1) if gap > UNHEARD_TOLERANCE_SEC else 0.0

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
        with self._lock:
            self._sent_audio.clear()
            self._sent_clock.clear()
            self.server_audio_sec = None
        self._terminated.clear()
        self.phase = "listening"

        client = StreamingClient(_client_options(self.api_key))
        self._client = client

        # Handlers before connect: a handshake rejection is dispatched as an
        # Error event rather than raised, and an unregistered handler at that
        # moment means a failed connection looks like silence.
        client.on(StreamingEvents.Begin, self._on_begin)
        client.on(StreamingEvents.Turn, self._on_turn)
        client.on(StreamingEvents.Warning, self._on_warning)
        client.on(StreamingEvents.Error, self._on_error)
        client.on(StreamingEvents.Termination, self._on_termination)

        connected = False

        try:
            client.connect(parameters)
            connected = True

            for chunk in chunks:
                if self._error:
                    break
                client.stream(chunk)
                self._audio_sent += len(chunk) / (SAMPLE_RATE * BYTES_PER_SAMPLE)
                with self._lock:
                    self._sent_audio.append(self._audio_sent)
                    self._sent_clock.append(time.monotonic() - self._started)
                if on_update:
                    on_update()

            # A short pause for a turn already on its way, then Terminate,
            # which releases the one unfinished turn the server still holds.
            # The pause restarts when a turn lands. See DRAIN_IDLE_SEC for
            # the measurements behind the length.
            self.phase = "draining"
            drain_started = time.monotonic()

            while not self._error:
                time.sleep(0.25)
                if on_update:
                    on_update()

                now = time.monotonic()
                quiet_since = max(self._last_turn_at or 0.0, drain_started)

                if now - quiet_since >= DRAIN_IDLE_SEC:
                    break
                if now - drain_started >= DRAIN_MAX_SEC:
                    log.warning("drain hit the %ss cap", DRAIN_MAX_SEC)
                    break

        finally:
            # Always terminate. An open socket bills until it times out.
            try:
                self.phase = "closing"
                # Waits up to TERMINATE_TIMEOUT_SEC for the Termination event.
                # Once this returns the SDK's read thread has stopped, so
                # waiting here any longer could never collect anything.
                client.disconnect(terminate=True)
                if connected and not self._error and not self._terminated.is_set():
                    log.warning(
                        "no Termination event within %ss", TERMINATE_TIMEOUT_SEC
                    )
            except Exception:
                log.exception("disconnect failed")
            finally:
                # Always release the audio source, however the session ended,
                # so the file behind it can be deleted.
                close = getattr(chunks, "close", None)
                if close:
                    try:
                        close()
                    except Exception:
                        log.exception("closing the audio source failed")
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