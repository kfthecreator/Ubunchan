"""Local model backend via Ollama's /api/chat with a JSON schema format."""

from __future__ import annotations

import requests

from ..config import OllamaConfig
from ..schema import RESPONSE_SCHEMA, WaifuResponse
from .base import LLMBackend, LLMError


class OllamaBackend(LLMBackend):
    def __init__(self, cfg: OllamaConfig, system_prompt: str, history_messages: int = 40):
        self.cfg = cfg
        self.system_prompt = system_prompt
        self.history_messages = history_messages
        self.messages: list[dict] = []

    def reset(self) -> None:
        self.messages = []

    def warmup(self) -> None:
        """Load the model and cache the system prompt, so the first real reply
        doesn't pay for loading the weights and reading the long prompt."""
        try:
            requests.post(f"{self.cfg.host.rstrip('/')}/api/chat", timeout=120, json={
                "model": self.cfg.model,
                "messages": [{"role": "system", "content": self.system_prompt},
                             {"role": "user", "content": "hi"}],
                "stream": False,
                "keep_alive": self.cfg.keep_alive,
                "options": {"temperature": self.cfg.temperature, "num_predict": 1},
            })
        except requests.RequestException:
            pass  # chat() reports a real error if Ollama is unreachable

    def chat(self, text: str) -> WaifuResponse:
        self.messages.append({"role": "user", "content": text})
        payload = {
            "model": self.cfg.model,
            "messages": [{"role": "system", "content": self.system_prompt}, *self.messages],
            "format": RESPONSE_SCHEMA,
            "stream": False,
            # Keep the model in memory between chats (Ollama unloads it after 5 min).
            "keep_alive": self.cfg.keep_alive,
            # Cap output so a runaway generation can never block the server.
            "options": {"temperature": self.cfg.temperature, "num_predict": 400},
        }
        try:
            r = requests.post(f"{self.cfg.host.rstrip('/')}/api/chat", json=payload, timeout=120)
            r.raise_for_status()
            content = r.json()["message"]["content"]
        except requests.RequestException as exc:
            self.messages.pop()
            raise LLMError(f"Ollama request failed ({exc}). Is `ollama serve` running and "
                           f"is the model '{self.cfg.model}' pulled?") from exc
        except (KeyError, ValueError) as exc:
            self.messages.pop()
            raise LLMError(f"Unexpected reply from Ollama: {exc}") from exc

        self.messages.append({"role": "assistant", "content": content})
        while len(self.messages) > self.history_messages:
            self.messages = self.messages[2:]
        return WaifuResponse.from_json(content)
