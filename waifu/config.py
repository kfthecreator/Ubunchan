"""Settings, loaded from config.toml (all keys optional)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_CONFIG_PATHS = (
    Path.cwd() / "config.toml",
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "waifu" / "config.toml",
)


@dataclass
class ClaudeConfig:
    model: str = "claude-opus-5"
    # Voice assistant: low effort keeps replies fast. Raise for harder tasks.
    effort: str = "low"
    max_tokens: int = 16000


@dataclass
class OllamaConfig:
    host: str = "http://localhost:11434"
    model: str = "llama3.2:3b"
    temperature: float = 0.4
    keep_alive: str = "30m"  # how long the model stays loaded after the last message


PROJECT_DIR = Path(__file__).resolve().parent.parent


@dataclass
class VoiceConfig:
    tts_enabled: bool = True
    tts_model: str = str(PROJECT_DIR / "models/piper/en_US-hfc_female-medium.onnx")
    tts_length_scale: float = 1.0  # < 1 speaks faster
    pitch: float = 1.06  # playback rate; slightly higher = cuter (also a bit faster)
    stt_enabled: bool = True
    stt_model: str = "base.en"  # tiny.en / base.en / small.en ...
    stt_device: str = "cpu"
    # "auto" = the input chosen in Ubuntu sound settings,
    # or a source name from `pactl list short sources`
    mic_device: str = "auto"
    auto_stop: bool = True  # stop listening after you finish talking
    silence_seconds: float = 1.3
    auto_send: bool = True  # send the transcript right away


@dataclass
class UIConfig:
    theme: str = "light"  # "light" (white) | "dark"
    width: int = 560
    height: int = 820
    show_terminal: bool = False


@dataclass
class Config:
    assistant_name: str = "Ubunchan"
    backend: str = "claude"  # "claude" | "ollama"
    # "off": confirm every command
    # "readonly": run read-only commands (ls, df, apt list ...) without asking
    # "normal": also run ordinary commands (apt install ...) without asking
    # Dangerous commands ALWAYS ask. Blocked commands NEVER run.
    autopilot: str = "off"
    command_timeout: int = 900
    max_chained_commands: int = 3
    output_chars_for_llm: int = 3000
    history_messages: int = 40
    working_dir: str = str(Path.home())
    audit_log: str = str(Path.home() / ".local/share/waifu/audit.log")
    vrm_path: str = str(Path.home() / "Downloads/Ubunchan1.vrm")
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    ui: UIConfig = field(default_factory=UIConfig)


def _apply(obj, data: dict):
    for f in fields(obj):
        if f.name not in data:
            continue
        value = data[f.name]
        current = getattr(obj, f.name)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _apply(current, value)
        else:
            setattr(obj, f.name, value)


def load_config(path: str | Path | None = None) -> Config:
    cfg = Config()
    candidates = [Path(path)] if path else list(DEFAULT_CONFIG_PATHS)
    for p in candidates:
        if p.is_file():
            with open(p, "rb") as f:
                _apply(cfg, tomllib.load(f))
            break
    if cfg.autopilot not in ("off", "readonly", "normal"):
        raise ValueError(f"autopilot must be off/readonly/normal, got {cfg.autopilot!r}")
    cfg.working_dir = os.path.expanduser(cfg.working_dir)
    cfg.audit_log = os.path.expanduser(cfg.audit_log)
    cfg.vrm_path = os.path.expanduser(cfg.vrm_path)
    cfg.voice.tts_model = os.path.expanduser(cfg.voice.tts_model)
    if not os.path.isabs(cfg.voice.tts_model):
        cfg.voice.tts_model = str(PROJECT_DIR / cfg.voice.tts_model)
    return cfg
