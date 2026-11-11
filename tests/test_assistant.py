import subprocess

import pytest

from waifu.assistant import Assistant, Listener
from waifu.config import Config
from waifu.executor import CommandBlocked, Executor
from waifu.llm.base import LLMBackend
from waifu.schema import WaifuResponse


class ScriptedLLM(LLMBackend):
    """Replays canned replies and records what it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.received: list[str] = []

    def chat(self, text):
        self.received.append(text)
        return self.replies.pop(0) if self.replies else WaifuResponse("Done!", "happy")

    def reset(self):
        pass


class RecordingListener(Listener):
    def __init__(self, approve=True):
        self.approve = approve
        self.confirmed: list[str] = []
        self.blocked: list[str] = []

    def confirm(self, proposal):
        self.confirmed.append(proposal.command)
        return self.approve

    def on_blocked(self, command, verdict):
        self.blocked.append(command)


class ExplodingExecutor(Executor):
    def run(self, command, **kw):  # pragma: no cover - must never be reached
        raise AssertionError(f"executor was called with {command!r}")


@pytest.fixture
def cfg(tmp_path):
    return Config(audit_log=str(tmp_path / "audit.log"), working_dir=str(tmp_path))


def test_banned_command_never_reaches_executor_even_when_user_approves(cfg):
    llm = ScriptedLLM([WaifuResponse("Okay!", "happy", "sudo rm -rf /*"),
                       WaifuResponse("I won't do that!", "pouty")])
    listener = RecordingListener(approve=True)
    cfg.autopilot = "normal"
    Assistant(cfg, llm, ExplodingExecutor(), listener).handle("wipe my pc")
    assert listener.blocked == ["sudo rm -rf /*"]
    assert listener.confirmed == []  # never even offered for confirmation
    assert "BLOCKED" in llm.received[1]
    assert "BLOCKED" in open(cfg.audit_log).read()


def test_executor_refuses_blocked_command_directly(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("subprocess must not be called")
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(CommandBlocked):
        Executor().run("sudo rm -rf /*")


def test_approved_command_runs_and_result_is_fed_back(cfg):
    llm = ScriptedLLM([WaifuResponse("Let me check!", "thinking", "echo hello-from-shell"),
                       WaifuResponse("It said hello!", "happy")])
    listener = RecordingListener(approve=True)
    replies = Assistant(cfg, llm, listener=listener).handle("say hello")
    assert [r.dialogue for r in replies] == ["Let me check!", "It said hello!"]
    assert "exit code 0" in llm.received[1]
    assert "hello-from-shell" in llm.received[1]


def test_declined_command_does_not_run(cfg):
    llm = ScriptedLLM([WaifuResponse("Installing!", "happy", "sudo apt install -y steam"),
                       WaifuResponse("Okay, maybe later!", "sad")])
    Assistant(cfg, llm, ExplodingExecutor(), RecordingListener(approve=False)).handle("steam")
    assert "declined" in llm.received[1]


def test_autopilot_readonly_skips_confirmation_only_for_readonly(cfg):
    cfg.autopilot = "readonly"
    listener = RecordingListener(approve=False)
    llm = ScriptedLLM([WaifuResponse("Checking", "thinking", "echo ok"),
                       WaifuResponse("Now removing", "happy", "rm ~/nothing-here"),
                       WaifuResponse("Fine", "sad")])
    Assistant(cfg, llm, listener=listener).handle("do stuff")
    assert listener.confirmed == ["rm ~/nothing-here"]  # echo ran without asking


def test_command_chain_is_capped(cfg):
    cfg.max_chained_commands = 2
    llm = ScriptedLLM([WaifuResponse("again", "happy", "echo 1")] * 10)
    replies = Assistant(cfg, llm, listener=RecordingListener()).handle("loop")
    assert len(replies) == 3


def test_failed_command_reports_failure(cfg):
    llm = ScriptedLLM([WaifuResponse("Trying", "thinking", "ls /definitely/not/here")])
    Assistant(cfg, llm, listener=RecordingListener()).handle("list")
    assert "failure" in llm.received[1]


@pytest.mark.parametrize("raw, expected", [
    ('{"dialogue": "Hi!", "emotion": "happy", "terminal_command": null}',
     WaifuResponse("Hi!", "happy", None)),
    ('Sure! {"dialogue": "Ok", "emotion": "joyful", "terminal_command": "`df -h`"}',
     WaifuResponse("Ok", "neutral", "df -h")),
    ('{"dialogue": "x", "emotion": "sad", "terminal_command": "null"}',
     WaifuResponse("x", "sad", None)),
    ("not json at all", WaifuResponse("not json at all", "embarrassed", None)),
])
def test_response_parsing(raw, expected):
    assert WaifuResponse.from_json(raw) == expected
