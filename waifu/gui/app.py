"""Desktop app entry point:  python -m waifu"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def _claude_credentials_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.environ.get("ANTHROPIC_PROFILE")
                or (Path.home() / ".config/anthropic").is_dir())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="waifu", description="Ubunchan desktop assistant")
    ap.add_argument("--config", help="path to config.toml")
    ap.add_argument("--backend", choices=["claude", "ollama"])
    ap.add_argument("--model", help="override the model for the chosen backend")
    ap.add_argument("--autopilot", choices=["off", "readonly", "normal"])
    ap.add_argument("--vrm", help="path to a .vrm model")
    ap.add_argument("--theme", choices=["dark", "light"])
    args = ap.parse_args(argv)

    if os.geteuid() == 0:
        print("Please don't run the assistant as root; it uses sudo when needed.")
        return 1

    from ..config import load_config
    cfg = load_config(args.config)
    notice = None
    if args.backend:
        cfg.backend = args.backend
    elif cfg.backend == "claude" and not _claude_credentials_available() and shutil.which("ollama"):
        cfg.backend = "ollama"
        notice = ("No Claude API key found (set ANTHROPIC_API_KEY), "
                  "so I'm using the local Ollama model.")
    if args.model:
        getattr(cfg, cfg.backend).model = args.model
    if args.autopilot:
        cfg.autopilot = args.autopilot
    if args.vrm:
        cfg.vrm_path = os.path.abspath(os.path.expanduser(args.vrm))
    if args.theme:
        cfg.ui.theme = args.theme

    # Qt imports after arg parsing so `--help` is instant.
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Ubunchan")
    app.setDesktopFileName("ubunchan")
    icon = Path(__file__).parent / "web" / "icon.png"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))

    if not os.path.isfile(cfg.vrm_path):
        QMessageBox.critical(None, "Model not found",
                             f"Couldn't find the VRM model:\n{cfg.vrm_path}\n\n"
                             "Set vrm_path in config.toml or pass --vrm.")
        return 1

    from ..assistant import Assistant
    from ..llm import create_backend
    from ..persona import build_system_prompt
    from .window import MainWindow

    def make_assistant(listener):
        llm = create_backend(cfg, build_system_prompt(cfg.assistant_name))
        return Assistant(cfg, llm, listener=listener)

    backend_label = f"{cfg.backend} · {getattr(cfg, cfg.backend).model}"
    win = MainWindow(cfg, make_assistant, backend_label)
    if notice:
        win._log_info(notice)
    win.show()

    shot = os.environ.get("WAIFU_SCREENSHOT")  # debugging aid: save a window grab
    if shot:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(int(os.environ.get("WAIFU_SCREENSHOT_DELAY", "9000")),
                          lambda: win.grab().save(shot))
    code = app.exec()
    # A worker may still be blocked in a network call; everything that matters
    # was cleaned up in closeEvent, so exit without waiting on it.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
