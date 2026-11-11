"""Text-to-speech with Piper (fast, local, runs on CPU)."""

from __future__ import annotations

import io
import os
import re
import wave


class TTSError(RuntimeError):
    pass


def clean_for_speech(text: str) -> str:
    """Strip things a voice shouldn't read out (markdown, backticks, emoji)."""
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"[*_#>~]+", "", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", "", text)
    return re.sub(r"\s+", " ", text).strip()


class PiperTTS:
    def __init__(self, model_path: str, length_scale: float = 1.0):
        from piper import PiperVoice  # heavy import, keep it lazy

        if not os.path.isfile(model_path):
            raise TTSError(f"Piper voice not found: {model_path}\n"
                           "Download one with: python -m piper.download_voices "
                           "--download-dir models/piper en_US-hfc_female-medium")
        self._voice = PiperVoice.load(model_path)
        self.length_scale = length_scale

    def synthesize(self, text: str) -> bytes:
        """Return WAV bytes for ``text`` (empty bytes if nothing to say)."""
        from piper import SynthesisConfig

        text = clean_for_speech(text)
        if not text:
            return b""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            self._voice.synthesize_wav(text, wav,
                                       syn_config=SynthesisConfig(length_scale=self.length_scale))
        return buf.getvalue()
