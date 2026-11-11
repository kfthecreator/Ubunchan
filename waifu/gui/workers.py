"""Background threads: the assistant (LLM + commands), TTS and STT.

Nothing slow runs on the GUI thread. Workers talk to the window only through
Qt signals. When the assistant needs a decision from the user (approve a
command, sudo password), the worker thread blocks on a PendingRequest until
the GUI answers it.
"""

from __future__ import annotations

import queue
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..assistant import Assistant, Listener, ProposedCommand
from ..executor import ExecResult
from ..safety import Verdict
from ..schema import WaifuResponse


@dataclass
class PendingRequest:
    """A question from a worker thread that the GUI must answer."""
    payload: Any = None
    result: Any = None
    _event: threading.Event = field(default_factory=threading.Event)

    def answer(self, result: Any) -> None:
        self.result = result
        self._event.set()

    def wait(self) -> Any:
        self._event.wait()
        return self.result


class AssistantWorker(QObject):
    reply = Signal(str, str)                 # dialogue, emotion
    blocked = Signal(str, list)              # command, reasons
    commandStarted = Signal(str)
    output = Signal(str, str)                # line, "stdout"/"stderr"
    commandDone = Signal(int, bool, float)   # exit code, ok, seconds
    error = Signal(str)
    busy = Signal(bool)
    confirmRequested = Signal(object)        # PendingRequest(payload=ProposedCommand)
    passwordRequested = Signal(object)       # PendingRequest

    def __init__(self, make_assistant):
        super().__init__()
        self._make_assistant = make_assistant
        self.assistant: Assistant | None = None
        self._pending: list[PendingRequest] = []

    # Listener implementation (runs on the worker thread) ------------------
    def _listener(self) -> Listener:
        worker = self

        class _GuiListener(Listener):
            def on_reply(self, r: WaifuResponse) -> None:
                worker.reply.emit(r.dialogue, r.emotion)

            def on_blocked(self, command: str, verdict: Verdict) -> None:
                worker.blocked.emit(command, list(verdict.reasons))

            def confirm(self, proposal: ProposedCommand) -> bool:
                return bool(worker._ask(worker.confirmRequested, proposal))

            def sudo_password(self) -> str | None:
                return worker._ask(worker.passwordRequested, None)

            def on_command_start(self, command: str) -> None:
                worker.commandStarted.emit(command)

            def on_output(self, line: str, stream: str) -> None:
                worker.output.emit(line, stream)

            def on_command_done(self, result: ExecResult) -> None:
                worker.commandDone.emit(result.exit_code, result.ok, result.duration)

            def on_error(self, message: str) -> None:
                worker.error.emit(message)

        return _GuiListener()

    def _ask(self, signal, payload):
        req = PendingRequest(payload)
        self._pending.append(req)
        signal.emit(req)
        try:
            return req.wait()
        finally:
            self._pending.remove(req)

    def release_pending(self) -> None:
        """Answer every open question with 'no' (used when the window closes)."""
        for req in list(self._pending):
            req.answer(None)

    # Slots (queued onto the worker thread) --------------------------------
    @Slot()
    def setup(self) -> None:
        try:
            self.assistant = self._make_assistant(self._listener())
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self.error.emit(f"Could not start the assistant: {exc}")
            return
        # Runs on this worker thread while the avatar loads; a message typed
        # meanwhile simply waits for it to finish.
        self.assistant.llm.warmup()

    @Slot(str)
    def handle(self, text: str) -> None:
        if not self.assistant:
            self.error.emit("The assistant is not ready.")
            return
        self.busy.emit(True)
        try:
            self.assistant.handle(text)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.error.emit(f"Something went wrong: {exc}")
        finally:
            self.busy.emit(False)

    @Slot()
    def reset(self) -> None:
        if self.assistant:
            self.assistant.llm.reset()

    def cancel_command(self) -> None:
        """Thread-safe: kill the running command."""
        if self.assistant:
            self.assistant.executor.cancel()


class TTSWorker(QThread):
    """Turns replies into WAV clips, in order, off the GUI thread."""
    clipReady = Signal(str, str, str)   # url ("" if no voice), text, emotion
    status = Signal(str)

    def __init__(self, voice_cfg, add_clip):
        super().__init__()
        self.cfg = voice_cfg
        self._add_clip = add_clip
        self._queue: queue.Queue = queue.Queue()
        self._tts = None

    def say(self, text: str, emotion: str) -> None:
        self._queue.put((text, emotion))

    def clear(self) -> None:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def stop(self) -> None:
        self._queue.put(None)

    def run(self) -> None:
        if self.cfg.tts_enabled:
            try:
                from ..voice.tts import PiperTTS
                self._tts = PiperTTS(self.cfg.tts_model, self.cfg.tts_length_scale)
            except Exception as exc:  # noqa: BLE001
                self.status.emit(f"Voice disabled: {exc}")
        while True:
            item = self._queue.get()
            if item is None:
                return
            text, emotion = item
            url = ""
            if self._tts:
                try:
                    wav = self._tts.synthesize(text)
                    url = self._add_clip(wav) if wav else ""
                except Exception as exc:  # noqa: BLE001
                    self.status.emit(f"TTS failed: {exc}")
            self.clipReady.emit(url, text, emotion)


class STTWorker(QObject):
    """Loads Whisper once, then transcribes recordings on its own thread."""
    ready = Signal(bool, str)        # ok, message
    transcribed = Signal(str)

    def __init__(self, voice_cfg):
        super().__init__()
        self.cfg = voice_cfg
        self._stt = None

    @Slot()
    def load(self) -> None:
        try:
            from ..voice.stt import WhisperSTT
            self._stt = WhisperSTT(self.cfg.stt_model, self.cfg.stt_device)
            self.ready.emit(True, "")
        except Exception as exc:  # noqa: BLE001
            self.ready.emit(False, str(exc))

    @Slot(object)
    def transcribe(self, audio) -> None:
        text = ""
        if self._stt is not None:
            try:
                text = self._stt.transcribe(audio)
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                text = ""
                self.ready.emit(True, f"transcription failed: {exc}")
        self.transcribed.emit(text)
