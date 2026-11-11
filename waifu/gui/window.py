"""Main desktop window: avatar on top, terminal drawer, control bar at the bottom."""

from __future__ import annotations

import html

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QPlainTextEdit, QPushButton, QSizePolicy, QTextEdit, QToolButton,
    QVBoxLayout, QWidget,
)

from ..assistant import ProposedCommand
from ..config import Config
from ..safety import Risk
from .avatar_view import AvatarView
from .server import AvatarServer
from .styles import RISK_COLORS, stylesheet
from .workers import AssistantWorker, PendingRequest, STTWorker, TTSWorker

STATUS_COLORS = {
    "ready": "#4ade80", "listening": "#f472b6", "transcribing": "#c084fc",
    "thinking": "#facc15", "running": "#60a5fa", "speaking": "#8b7cff",
    "error": "#f87171", "loading": "#94a3b8",
}


class ConfirmDialog(QDialog):
    """'I'm going to run: ... Should I proceed?'"""

    def __init__(self, proposal: ProposedCommand, name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{name} wants to run a command")
        self.setModal(True)
        self.setMinimumWidth(460)
        v = proposal.verdict
        risk_color = RISK_COLORS[v.risk.name]

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.setContentsMargins(20, 18, 20, 18)

        title = QLabel(f"I'm going to run this command. Should I proceed?")
        title.setObjectName("dialogTitle")
        title.setWordWrap(True)
        lay.addWidget(title)

        cmd = QTextEdit()
        cmd.setObjectName("commandBox")
        cmd.setReadOnly(True)
        cmd.setPlainText(proposal.command)
        cmd.setFixedHeight(min(140, 38 + 20 * proposal.command.count("\n")
                               + 20 * (len(proposal.command) // 52)))
        lay.addWidget(cmd)

        risk = QLabel(f"<b style='color:{risk_color}'>● {v.risk.name.replace('_', '-')}</b>"
                      + (f"<br><span style='color:#9a9cb5'>{html.escape('; '.join(v.reasons))}"
                         "</span>" if v.reasons else ""))
        risk.setWordWrap(True)
        lay.addWidget(risk)

        self.yes_box = None
        if v.risk is Risk.DANGEROUS:
            warn = QLabel("This is a <b>dangerous</b> command. Type <b>yes</b> to allow it.")
            warn.setWordWrap(True)
            lay.addWidget(warn)
            self.yes_box = QLineEdit()
            self.yes_box.setPlaceholderText("yes")
            lay.addWidget(self.yes_box)

        row = QHBoxLayout()
        row.addStretch(1)
        self.deny = QPushButton("Don't run")
        self.run = QPushButton("Run it")
        self.run.setObjectName("primary" if v.risk is not Risk.DANGEROUS else "danger")
        self.deny.clicked.connect(self.reject)
        self.run.clicked.connect(self.accept)
        row.addWidget(self.deny)
        row.addWidget(self.run)
        lay.addLayout(row)

        if self.yes_box:
            self.run.setEnabled(False)
            self.yes_box.textChanged.connect(
                lambda t: self.run.setEnabled(t.strip().lower() == "yes"))
            self.yes_box.setFocus()
        else:
            self.deny.setFocus()


class _MicSignals(QObject):
    level = Signal(float)
    autoStopped = Signal()
    warning = Signal(str)


class MainWindow(QMainWindow):
    sendToAssistant = Signal(str)
    transcribeRequested = Signal(object)

    def __init__(self, cfg: Config, make_assistant, backend_label: str):
        super().__init__()
        self.cfg = cfg
        self.name = cfg.assistant_name
        self.setWindowTitle(self.name)
        self.resize(cfg.ui.width, cfg.ui.height)
        self.setStyleSheet(stylesheet(cfg.ui.theme))

        self._busy = False
        self._speaking = False
        self._recorder = None
        self._stt_ready = False
        self._closing = False

        # ---- local server + avatar -------------------------------------
        self.server = AvatarServer(cfg.vrm_path).start()
        self.avatar = AvatarView(self.server.url, cfg.ui.theme)
        self.avatar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.avatar.loaded.connect(lambda n: self._log_info(f"Model loaded: {n}"))
        self.avatar.failed.connect(lambda m: self._log_error(f"Avatar: {m}"))
        self.avatar.speechStarted.connect(self._on_speech_started)
        self.avatar.speechEnded.connect(self._on_speech_ended)

        # ---- terminal drawer -------------------------------------------
        self.drawer = QFrame()
        self.drawer.setObjectName("drawer")
        dl = QVBoxLayout(self.drawer)
        dl.setContentsMargins(12, 8, 12, 8)
        head = QHBoxLayout()
        head_label = QLabel("Activity & terminal output")
        head_label.setObjectName("muted")
        head.addWidget(head_label)
        head.addStretch(1)
        self.cancel_btn = QPushButton("Stop command")
        self.cancel_btn.setObjectName("danger")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel_command)
        head.addWidget(self.cancel_btn)
        dl.addLayout(head)
        self.log = QPlainTextEdit()
        self.log.setObjectName("terminal")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        mono = QFont("Ubuntu Mono")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(10)
        self.log.setFont(mono)
        dl.addWidget(self.log)
        self.drawer.setFixedHeight(210)
        self.drawer.setVisible(cfg.ui.show_terminal)

        # ---- bottom bar ------------------------------------------------
        bar = QFrame()
        bar.setObjectName("bottomBar")
        bl = QVBoxLayout(bar)
        bl.setContentsMargins(14, 12, 14, 10)
        bl.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.mic_btn = QToolButton()
        self.mic_btn.setObjectName("micButton")
        self.mic_btn.setText("🎙")
        self.mic_btn.setCheckable(True)
        self.mic_btn.setFixedSize(46, 46)
        self.mic_btn.setToolTip("Talk (Ctrl+M). Click again to stop.")
        self.mic_btn.clicked.connect(self._toggle_mic)
        self.mic_btn.setEnabled(False)
        row.addWidget(self.mic_btn)

        self.input = QLineEdit()
        self.input.setObjectName("promptInput")
        self.input.setPlaceholderText(f"Ask {self.name} anything, or tell her what to do…")
        self.input.returnPressed.connect(self._send_typed)
        self.input.setMinimumHeight(46)
        row.addWidget(self.input, 1)

        self.send_btn = QToolButton()
        self.send_btn.setObjectName("sendButton")
        self.send_btn.setText("➤")
        self.send_btn.setFixedSize(46, 46)
        self.send_btn.setToolTip("Send (Enter)")
        self.send_btn.clicked.connect(self._send_typed)
        row.addWidget(self.send_btn)
        bl.addLayout(row)

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self.status_dot = QLabel("●")
        self.status_label = QLabel()
        self.status_label.setObjectName("statusLabel")
        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status_label, 1)
        backend = QLabel(backend_label)
        backend.setObjectName("chip")
        backend.setToolTip("LLM backend (change it in config.toml)")
        status_row.addWidget(backend)
        self.autopilot = QComboBox()
        self.autopilot.setToolTip("Autopilot: which commands may run without asking.\n"
                                  "Dangerous commands always ask. Blocked ones never run.")
        for label, value in (("Ask every time", "off"), ("Auto: read-only", "readonly"),
                             ("Auto: normal", "normal")):
            self.autopilot.addItem(label, value)
        self.autopilot.setCurrentIndex(max(0, self.autopilot.findData(cfg.autopilot)))
        self.autopilot.currentIndexChanged.connect(
            lambda _: setattr(cfg, "autopilot", self.autopilot.currentData()))
        status_row.addWidget(self.autopilot)
        self.term_btn = QToolButton()
        self.term_btn.setObjectName("chipButton")
        self.term_btn.setText("Terminal")
        self.term_btn.setCheckable(True)
        self.term_btn.setChecked(cfg.ui.show_terminal)
        self.term_btn.toggled.connect(self.drawer.setVisible)
        status_row.addWidget(self.term_btn)
        bl.addLayout(status_row)

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self.avatar, 1)
        cl.addWidget(self.drawer)
        cl.addWidget(bar)
        self.setCentralWidget(central)

        QShortcut(QKeySequence("Ctrl+M"), self, activated=self._toggle_mic_shortcut)
        QShortcut(QKeySequence("Escape"), self, activated=self._stop_speaking)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.log.clear)

        # ---- assistant worker thread -------------------------------------
        self.assistant_thread = QThread(self)
        self.worker = AssistantWorker(make_assistant)
        self.worker.moveToThread(self.assistant_thread)
        self.assistant_thread.started.connect(self.worker.setup)
        self.sendToAssistant.connect(self.worker.handle)
        self.worker.reply.connect(self._on_reply)
        self.worker.blocked.connect(self._on_blocked)
        self.worker.commandStarted.connect(self._on_command_started)
        self.worker.output.connect(self._on_output)
        self.worker.commandDone.connect(self._on_command_done)
        self.worker.error.connect(self._on_error)
        self.worker.busy.connect(self._on_busy)
        self.worker.confirmRequested.connect(self._on_confirm)
        self.worker.passwordRequested.connect(self._on_password)
        self.assistant_thread.start()

        # ---- voice out -----------------------------------------------------
        self.tts = TTSWorker(cfg.voice, self.server.add_clip)
        self.tts.clipReady.connect(
            lambda url, text, emo: self.avatar.speak(text, emo, url, cfg.voice.pitch))
        self.tts.status.connect(self._log_error)
        self.tts.start()

        # ---- voice in ------------------------------------------------------
        self.mic_signals = _MicSignals()
        self.mic_signals.level.connect(self._on_mic_level)
        self.mic_signals.autoStopped.connect(self._stop_listening)
        self.mic_signals.warning.connect(self._on_error)
        from ..voice.stt import pick_input_device
        self._mic_device = pick_input_device(cfg.voice.mic_device)
        self.stt_thread = QThread(self)
        self.stt = STTWorker(cfg.voice)
        self.stt.moveToThread(self.stt_thread)
        self.stt.ready.connect(self._on_stt_ready)
        self.stt.transcribed.connect(self._on_transcribed)
        self.transcribeRequested.connect(self.stt.transcribe)
        if cfg.voice.stt_enabled:
            self.stt_thread.started.connect(self.stt.load)
            self.stt_thread.start()
            self.mic_btn.setToolTip("Loading speech recognition…")
        else:
            self.mic_btn.setToolTip("Speech input is disabled in config.toml")

        self._set_status("loading", "Waking up…")
        QTimer.singleShot(1500, lambda: self._set_status("ready", "Ready")
                          if not self._busy else None)
        self.input.setFocus()

    # ================================================================ status
    def _set_status(self, kind: str, text: str) -> None:
        self.status_dot.setStyleSheet(f"color: {STATUS_COLORS.get(kind, '#94a3b8')};")
        self.status_label.setText(text)

    def _refresh_status(self) -> None:
        if self._recorder:
            self._set_status("listening", "Listening… (click the mic to stop)")
        elif self._busy:
            self._set_status("thinking", "Thinking…")
        elif self._speaking:
            self._set_status("speaking", "Speaking… (Esc to stop)")
        else:
            self._set_status("ready", "Ready")
            self.avatar.set_state("idle")

    # ================================================================ log
    def _append(self, html_line: str) -> None:
        self.log.appendHtml(html_line)
        self.log.moveCursor(QTextCursor.MoveOperation.End)

    def _log_info(self, text: str) -> None:
        self._append(f"<span style='color:#7d7f99'>{html.escape(text)}</span>")

    def _log_error(self, text: str) -> None:
        self._append(f"<span style='color:#f87171'>! {html.escape(text)}</span>")

    # ================================================================ input
    def _send_typed(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self._submit(text)

    def _submit(self, text: str) -> None:
        if text in ("/reset", "/forget"):
            self.worker.reset()
            self._log_info("(conversation forgotten)")
            return
        self._stop_speaking()
        self._append(f"<b style='color:#60a5fa'>you:</b> {html.escape(text)}")
        self.sendToAssistant.emit(text)

    # ================================================================ worker events
    def _on_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self.avatar.set_state("thinking")
        self._refresh_status()

    def _on_reply(self, dialogue: str, emotion: str) -> None:
        self._append(f"<b style='color:#c4b5fd'>{html.escape(self.name)}</b> "
                     f"<span style='color:#7d7f99'>[{emotion}]</span>: {html.escape(dialogue)}")
        self.tts.say(dialogue, emotion)

    def _on_blocked(self, command: str, reasons: list) -> None:
        self._append(f"<b style='color:#f87171'>⛔ BLOCKED:</b> <code>{html.escape(command)}</code>")
        for r in reasons:
            self._append(f"<span style='color:#f87171'>&nbsp;&nbsp;- {html.escape(r)}</span>")
        self._set_status("error", "Blocked a dangerous command")

    def _on_command_started(self, command: str) -> None:
        self.term_btn.setChecked(True)
        self.cancel_btn.setVisible(True)
        self.avatar.set_state("working")
        self._append(f"<span style='color:#4ade80'>$</span> <b>{html.escape(command)}</b>")
        short = command if len(command) < 48 else command[:45] + "…"
        self._set_status("running", f"Running: {short}")

    def _on_output(self, line: str, stream: str) -> None:
        color = "#fca5a5" if stream == "stderr" else "#d4d4e0"
        self._append(f"<span style='color:{color}'>{html.escape(line) or '&nbsp;'}</span>")

    def _on_command_done(self, code: int, ok: bool, seconds: float) -> None:
        self.cancel_btn.setVisible(False)
        mark = "<span style='color:#4ade80'>✔</span>" if ok else "<span style='color:#f87171'>✘</span>"
        self._append(f"{mark} <span style='color:#7d7f99'>exit {code} in {seconds:.1f}s</span>")
        self._set_status("thinking", "Thinking about the result…")

    def _on_error(self, message: str) -> None:
        self._log_error(message)
        self._set_status("error", message if len(message) < 70 else message[:67] + "…")
        self.avatar.set_emotion("embarrassed")

    def _on_confirm(self, req: PendingRequest) -> None:
        if self._closing:
            req.answer(False)
            return
        self.raise_()
        self.activateWindow()
        dlg = ConfirmDialog(req.payload, self.name, self)
        approved = dlg.exec() == QDialog.DialogCode.Accepted
        self._log_info("approved" if approved else "declined")
        req.answer(approved)

    def _on_password(self, req: PendingRequest) -> None:
        if self._closing:
            req.answer(None)
            return
        pw, ok = QInputDialog.getText(
            self, "Administrator password",
            "This command needs sudo.\nEnter your password (it is never sent to the AI):",
            QLineEdit.EchoMode.Password)
        req.answer(pw if ok and pw else None)

    def _cancel_command(self) -> None:
        self.worker.cancel_command()
        self._log_info("stopping command…")

    # ================================================================ speech out
    def _on_speech_started(self) -> None:
        self._speaking = True
        self._refresh_status()

    def _on_speech_ended(self) -> None:
        self._speaking = False
        self._refresh_status()

    def _stop_speaking(self) -> None:
        self.tts.clear()
        self.avatar.stop_speaking()

    # ================================================================ speech in
    def _on_stt_ready(self, ok: bool, message: str) -> None:
        if ok and not message:
            self._stt_ready = True
            self.mic_btn.setEnabled(True)
            self.mic_btn.setToolTip("Talk (Ctrl+M). Stops by itself when you finish.")
            self._log_info(f"Speech recognition ready (mic: {self._mic_device or 'system default'}).")
        elif message:
            self._log_error(f"Speech recognition: {message}")
            if not ok:
                self.mic_btn.setToolTip(f"Speech input unavailable: {message}")

    def _toggle_mic_shortcut(self) -> None:
        if self.mic_btn.isEnabled():
            self.mic_btn.setChecked(not self.mic_btn.isChecked())
            self._toggle_mic()

    def _toggle_mic(self) -> None:
        if self._recorder:
            self._stop_listening()
        else:
            self._start_listening()

    def _start_listening(self) -> None:
        from ..voice.stt import Recorder, STTError
        self._stop_speaking()  # barge-in: she stops talking when you start
        try:
            self._recorder = Recorder(
                auto_stop=self.cfg.voice.auto_stop,
                silence_seconds=self.cfg.voice.silence_seconds,
                on_level=self.mic_signals.level.emit,
                on_auto_stop=self.mic_signals.autoStopped.emit,
                device=self._mic_device,
                on_warning=self.mic_signals.warning.emit)
            self._recorder.start()
        except STTError as exc:
            self._recorder = None
            self.mic_btn.setChecked(False)
            self._on_error(str(exc))
            return
        self.mic_btn.setChecked(True)
        self.avatar.set_state("listening")
        self._refresh_status()

    def _stop_listening(self) -> None:
        rec, self._recorder = self._recorder, None
        self.mic_btn.setChecked(False)
        self.mic_btn.setStyleSheet("")
        if not rec:
            return
        audio = rec.stop()
        self._set_status("transcribing", "Transcribing…")
        self.avatar.set_state("thinking")
        self.transcribeRequested.emit(audio)

    def _on_mic_level(self, level: float) -> None:
        if self._recorder:
            glow = int(40 + 160 * level)
            self.mic_btn.setStyleSheet(
                f"QToolButton#micButton {{ border: 2px solid rgba(244,114,182,{glow}); }}")

    def _on_transcribed(self, text: str) -> None:
        if not text:
            self._log_info("(didn't catch that)")
            self.avatar.set_state("idle")
            self._refresh_status()
            return
        if self.cfg.voice.auto_send:
            self._submit(text)
        else:
            self.input.setText(text)
            self.input.setFocus()
            self._refresh_status()

    # ================================================================ shutdown
    def closeEvent(self, event) -> None:  # noqa: N802
        self._closing = True
        if self._recorder:
            self._recorder.stop()
            self._recorder = None
        self.worker.release_pending()
        self.worker.cancel_command()
        self.tts.stop()
        self.tts.wait(2000)
        for t in (self.assistant_thread, self.stt_thread):
            t.quit()
            t.wait(3000)
        self.server.stop()
        super().closeEvent(event)
