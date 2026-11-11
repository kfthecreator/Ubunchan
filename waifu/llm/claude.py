"""Claude backend (Anthropic API) using structured JSON outputs."""

from __future__ import annotations

import anthropic

from ..config import ClaudeConfig
from ..schema import RESPONSE_SCHEMA, WaifuResponse
from .base import LLMBackend, LLMError


class ClaudeBackend(LLMBackend):
    def __init__(self, cfg: ClaudeConfig, system_prompt: str, history_messages: int = 40):
        self.cfg = cfg
        self.system_prompt = system_prompt
        self.history_messages = history_messages
        self.messages: list[dict] = []
        # Reads ANTHROPIC_API_KEY, or a profile from `ant auth login`.
        self.client = anthropic.Anthropic()

    def reset(self) -> None:
        self.messages = []

    def _trim(self) -> None:
        # Drop the oldest user/assistant pairs; history must start with a user turn.
        while len(self.messages) > self.history_messages:
            self.messages = self.messages[2:]

    def chat(self, text: str) -> WaifuResponse:
        self.messages.append({"role": "user", "content": text})
        try:
            response = self.client.beta.messages.create(
                model=self.cfg.model,
                max_tokens=self.cfg.max_tokens,
                system=self.system_prompt,
                messages=self.messages,
                thinking={"type": "adaptive"},
                output_config={
                    "effort": self.cfg.effort,
                    "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
                },
                cache_control={"type": "ephemeral"},
                # If a safety classifier declines, retry server-side on the
                # recommended fallback model instead of failing.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as exc:
            self.messages.pop()
            raise LLMError("Claude API key missing or invalid. Set ANTHROPIC_API_KEY "
                           "or run `ant auth login`.") from exc
        except anthropic.RateLimitError as exc:
            self.messages.pop()
            raise LLMError("Claude rate limit hit, try again in a moment.") from exc
        except anthropic.APIStatusError as exc:
            self.messages.pop()
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            self.messages.pop()
            raise LLMError("Could not reach the Claude API (network problem?).") from exc

        if response.stop_reason == "refusal":
            self.messages.pop()
            return WaifuResponse("Sorry, I can't help with that one.", "embarrassed", None)

        # Keep the full content (including thinking blocks) in history.
        self.messages.append({"role": "assistant", "content": response.content})
        self._trim()

        text_out = next((b.text for b in response.content if b.type == "text"), "")
        if response.stop_reason == "max_tokens" and not text_out:
            return WaifuResponse("Oops, I ran out of breath there. Can you ask again?",
                                 "embarrassed", None)
        return WaifuResponse.from_json(text_out)
