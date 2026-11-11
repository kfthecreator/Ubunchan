"""Microphone capture (PipeWire parecord, arecord fallback) and Whisper transcription."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from typing import Callable

import numpy as np

SAMPLE_RATE = 16000
FRAME = 1600  # 100 ms of 16-bit mono samples
SETTLE_CHUNKS = 3  # ignore the first 300 ms while the input settles


class STTError(RuntimeError):
    pass


class _HighPass:
    """Removes DC offset and rumble below ~50 Hz, chunk by chunk.

    Some onboard mic inputs (e.g. the rear jack on AMD boards) start every
    recording with a large low-frequency thump that slowly settles. Left in, it
    fools the speech detector and Whisper. Subtracting a 20 ms moving average
    keeps the voice and drops the drift.
    """

    def __init__(self, window: int = 320):
        self.window = window
        self._tail = None

    def __call__(self, x: np.ndarray) -> np.ndarray:
        if self._tail is None:
            self._tail = np.full(self.window - 1, x[0] if x.size else 0.0, dtype=np.float32)
        buf = np.concatenate([self._tail, x])
        mean = np.convolve(buf, np.ones(self.window, dtype=np.float32) / self.window, "valid")
        self._tail = buf[-(self.window - 1):]
        return (x - mean).astype(np.float32)


def pick_input_device(configured: str = "auto") -> str | None:
    """Resolve the mic to use. "auto"/"default" follow the input device chosen in
    Ubuntu's sound settings; anything else is a source name from
    `pactl list short sources`. Returns None for "the system default"."""
    if configured and configured not in ("auto", "default"):
        return configured
    return None


class Recorder:
    """Records 16 kHz mono audio until stop() is called or the speaker goes quiet.

    With ``auto_stop`` the recording ends by itself after ``silence_seconds``
    of quiet that follows detected speech, so one click is enough to talk.
    """

    def __init__(self, auto_stop: bool = True, silence_seconds: float = 1.3,
                 max_seconds: float = 30.0, on_level: Callable[[float], None] | None = None,
                 on_auto_stop: Callable[[], None] | None = None, device: str | None = None,
                 on_warning: Callable[[str], None] | None = None):
        if shutil.which("parecord"):  # PipeWire / PulseAudio (Ubuntu default)
            self._cmd = ["parecord", "--raw", "--format=s16le", f"--rate={SAMPLE_RATE}",
                         "--channels=1", "--latency-msec=50"]
            if device:
                self._cmd.append(f"--device={device}")
        elif shutil.which("arecord"):
            self._cmd = ["arecord", "-q", "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1",
                         "-t", "raw"]
            if device:
                self._cmd += ["-D", device]
        else:
            raise STTError("No recorder found (sudo apt install -y pulseaudio-utils)")
        self.on_warning = on_warning
        self.auto_stop = auto_stop
        self.silence_seconds = silence_seconds
        self.max_seconds = max_seconds
        self.on_level = on_level
        self.on_auto_stop = on_auto_stop
        self._proc: subprocess.Popen | None = None
        self._chunks: list[np.ndarray] = []
        self._thread: threading.Thread | None = None

    @property
    def recording(self) -> bool:
        return self._proc is not None

    def start(self) -> None:
        if self._proc:
            return
        self._chunks = []
        self._proc = subprocess.Popen(self._cmd, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL)
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        proc = self._proc
        assert proc and proc.stdout
        highpass = _HighPass()
        noise_floor = None
        heard_speech = False
        quiet_since = None
        clipped = total = 0
        started = time.monotonic()
        while True:
            data = proc.stdout.read(FRAME * 2)
            if not data:
                break
            raw = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            clipped += int(np.count_nonzero(np.abs(raw) > 0.98))
            total += raw.size
            samples = highpass(raw)
            self._chunks.append(samples)
            if len(self._chunks) <= SETTLE_CHUNKS:
                continue  # let the input settle before judging levels
            rms = float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0

            # Adaptive noise floor: drops to quiet moments at once, creeps up slowly,
            # so it works for loud and very quiet mics alike.
            noise_floor = rms if noise_floor is None else min(rms, noise_floor * 1.02)
            noise_floor = max(noise_floor, 1e-4)
            if self.on_level:
                self.on_level(min(1.0, rms / (noise_floor * 8)))

            is_speech = rms > noise_floor * 3.0 and rms > 0.002
            now = time.monotonic()
            if is_speech:
                heard_speech = True
                quiet_since = None
            elif heard_speech:
                quiet_since = quiet_since or now
            if self.auto_stop and ((heard_speech and quiet_since and
                                    now - quiet_since >= self.silence_seconds)
                                   or now - started >= self.max_seconds):
                if self.on_auto_stop:
                    self.on_auto_stop()
                break
        if self.on_warning and total and clipped / total > 0.05:
            self.on_warning("Your microphone is clipping (too loud). Lower Input Volume in "
                            "Settings → Sound.")

    def stop(self) -> np.ndarray:
        """Stop recording and return float32 samples at 16 kHz."""
        proc, self._proc = self._proc, None
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        if len(self._chunks) <= SETTLE_CHUNKS:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(self._chunks[SETTLE_CHUNKS:])
        peak = float(np.abs(audio).max())
        if self.on_warning and peak < 0.02:
            self.on_warning("Your microphone is very quiet. Raise Input Volume in "
                            "Settings → Sound (about 70% works well).")
        # Normalise so Whisper hears a quiet mic as clearly as a loud one
        # (capped, so pure background noise isn't blown up into "speech").
        if peak > 1e-4:
            audio = audio * min(0.9 / peak, 50.0)
        return audio.astype(np.float32)


class WhisperSTT:
    """faster-whisper; the model is downloaded on first use (~150 MB for base.en)."""

    def __init__(self, model: str = "base.en", device: str = "cpu", compute_type: str = "int8"):
        from faster_whisper import WhisperModel

        self._model = WhisperModel(model, device=device, compute_type=compute_type)

    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size < SAMPLE_RATE * 0.3:
            return ""
        segments, _info = self._model.transcribe(
            audio, language="en", beam_size=1, vad_filter=True,
            condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segments).strip()
        # Whisper hallucinates these on silence.
        if text.lower().strip(" .!") in ("", "you", "thank you", "thanks for watching", "bye"):
            return ""
        return text
