"""QWebEngineView that hosts the three-vrm avatar page."""

from __future__ import annotations

import json

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QColor
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

BRIDGE_PREFIX = "@@waifu "


class _Page(QWebEnginePage):
    event = Signal(dict)

    def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: N802
        if message.startswith(BRIDGE_PREFIX):
            try:
                self.event.emit(json.loads(message[len(BRIDGE_PREFIX):]))
            except json.JSONDecodeError:
                pass
        elif level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            print(f"[avatar js] {message} ({source}:{line})")


class AvatarView(QWebEngineView):
    """Python-side API for the avatar: speak(), set_emotion(), set_state()."""

    loaded = Signal(str)          # model name
    failed = Signal(str)
    speechStarted = Signal()
    speechEnded = Signal()

    def __init__(self, base_url: str, theme: str = "dark", parent=None):
        super().__init__(parent)
        self._ready = False
        self._queued: list[str] = []
        page = _Page(self)
        page.setBackgroundColor(QColor("#ffffff"))
        page.event.connect(self._on_event)
        self.setPage(page)
        s = page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
        self.setContextMenuPolicy(self.contextMenuPolicy().NoContextMenu)
        self.load(QUrl(f"{base_url}?theme={theme}"))

    # -- API ----------------------------------------------------------------
    def speak(self, text: str, emotion: str, url: str | None, pitch: float = 1.0) -> None:
        self._call("speak", {"url": url or None, "text": text, "emotion": emotion, "pitch": pitch})

    def stop_speaking(self) -> None:
        self._call("stopSpeaking")

    def set_emotion(self, emotion: str) -> None:
        self._call("setEmotion", emotion)

    def set_state(self, state: str) -> None:
        """idle | listening | thinking | working"""
        self._call("setState", state)

    # -- internals ------------------------------------------------------------
    def _call(self, fn: str, *args) -> None:
        js = f"window.waifu && window.waifu.{fn}({', '.join(json.dumps(a) for a in args)});"
        if self._ready:
            self.page().runJavaScript(js)
        else:
            self._queued.append(js)

    def _on_event(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "ready":
            self._ready = True
            for js in self._queued:
                self.page().runJavaScript(js)
            self._queued.clear()
        elif kind == "loaded":
            self.loaded.emit(ev.get("name", ""))
        elif kind == "error":
            self.failed.emit(ev.get("message", "unknown error"))
        elif kind == "speechStart":
            self.speechStarted.emit()
        elif kind == "speechEnd":
            self.speechEnded.emit()
