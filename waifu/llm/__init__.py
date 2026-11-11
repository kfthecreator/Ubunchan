from __future__ import annotations

from ..config import Config
from .base import LLMBackend, LLMError


def create_backend(cfg: Config, system_prompt: str) -> LLMBackend:
    if cfg.backend == "claude":
        from .claude import ClaudeBackend
        return ClaudeBackend(cfg.claude, system_prompt, cfg.history_messages)
    if cfg.backend == "ollama":
        from .ollama import OllamaBackend
        return OllamaBackend(cfg.ollama, system_prompt, cfg.history_messages)
    raise ValueError(f"unknown backend {cfg.backend!r} (use 'claude' or 'ollama')")


__all__ = ["LLMBackend", "LLMError", "create_backend"]
