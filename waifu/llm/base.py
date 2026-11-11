from __future__ import annotations

from abc import ABC, abstractmethod

from ..schema import WaifuResponse


class LLMError(RuntimeError):
    pass


class LLMBackend(ABC):
    """A stateful conversation that returns structured WaifuResponses."""

    @abstractmethod
    def chat(self, text: str) -> WaifuResponse:
        """Send a user (or [SYSTEM REPORT]) message and get the reply."""

    @abstractmethod
    def reset(self) -> None:
        """Forget the conversation."""

    def warmup(self) -> None:
        """Optionally preload the model so the first reply is fast."""
