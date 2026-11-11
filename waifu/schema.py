"""The structured reply every LLM backend must produce."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# Emotions the avatar knows how to show. Phase 3 maps these onto VRM
# expression presets (happy, angry, sad, relaxed, surprised, neutral).
EMOTIONS = (
    "neutral", "happy", "excited", "thinking", "surprised",
    "mischievous", "sad", "worried", "embarrassed", "pouty",
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "dialogue": {
            "type": "string",
            "description": "What the assistant says out loud, in English.",
        },
        "emotion": {"type": "string", "enum": list(EMOTIONS)},
        "terminal_command": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "Exact Ubuntu shell command to run, or null.",
        },
    },
    "required": ["dialogue", "emotion", "terminal_command"],
    "additionalProperties": False,
}


@dataclass
class WaifuResponse:
    dialogue: str
    emotion: str = "neutral"
    terminal_command: str | None = None

    @classmethod
    def from_json(cls, text: str) -> "WaifuResponse":
        """Parse model output, tolerating small models that wrap JSON in prose."""
        data = None
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            match = re.search(r"\{.*\}", text or "", re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                except json.JSONDecodeError:
                    data = None
        if not isinstance(data, dict):
            return cls(dialogue=(text or "").strip() or "Hmm, I got confused...",
                       emotion="embarrassed")

        dialogue = str(data.get("dialogue") or "").strip() or "..."
        emotion = str(data.get("emotion") or "neutral").lower()
        if emotion not in EMOTIONS:
            emotion = "neutral"
        cmd = data.get("terminal_command")
        if not isinstance(cmd, str) or not cmd.strip() or cmd.strip().lower() in ("null", "none"):
            cmd = None
        else:
            cmd = cmd.strip()
            # Models sometimes wrap the command in backticks.
            if cmd.startswith("`") and cmd.endswith("`"):
                cmd = cmd.strip("`").strip()
        return cls(dialogue=dialogue, emotion=emotion, terminal_command=cmd)
