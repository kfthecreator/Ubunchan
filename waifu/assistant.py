"""Orchestrates: user text -> LLM -> safety check -> confirm -> execute -> feedback."""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass

from .config import Config
from .executor import CommandBlocked, ExecResult, Executor
from .llm.base import LLMBackend, LLMError
from .safety import Risk, Verdict, analyze
from .schema import WaifuResponse


@dataclass
class ProposedCommand:
    command: str
    verdict: Verdict
    dialogue: str


class Listener:
    """UI hooks. The CLI implements these now; the desktop UI will in Phase 4."""

    def on_reply(self, reply: WaifuResponse) -> None: ...

    def on_blocked(self, command: str, verdict: Verdict) -> None: ...

    def confirm(self, proposal: ProposedCommand) -> bool:
        return False  # safe default: never run without an explicit yes

    def sudo_password(self) -> str | None:
        return None

    def on_command_start(self, command: str) -> None: ...

    def on_output(self, line: str, stream: str) -> None: ...

    def on_command_done(self, result: ExecResult) -> None: ...

    def on_error(self, message: str) -> None: ...


AUTOPILOT_MAX_RISK = {"off": None, "readonly": Risk.READ_ONLY, "normal": Risk.NORMAL}


class Assistant:
    def __init__(self, cfg: Config, llm: LLMBackend, executor: Executor | None = None,
                 listener: Listener | None = None):
        self.cfg = cfg
        self.llm = llm
        self.executor = executor or Executor(cfg.working_dir, cfg.command_timeout)
        self.listener = listener or Listener()

    # ------------------------------------------------------------------

    def handle(self, user_text: str) -> list[WaifuResponse]:
        """Process one user utterance. Returns every reply spoken during it."""
        replies: list[WaifuResponse] = []
        reply = self._ask(user_text)
        if reply is None:
            return replies
        replies.append(reply)

        steps = 0
        while reply.terminal_command:
            if steps >= self.cfg.max_chained_commands:
                self.listener.on_error(
                    f"Stopped after {steps} commands in a row; ask again to continue.")
                break
            steps += 1
            report = self._process_command(reply)
            reply = self._ask(report)
            if reply is None:
                break
            replies.append(reply)
        return replies

    # ------------------------------------------------------------------

    def _ask(self, text: str) -> WaifuResponse | None:
        try:
            reply = self.llm.chat(text)
        except LLMError as exc:
            self.listener.on_error(str(exc))
            return None
        self.listener.on_reply(reply)
        return reply

    def _needs_confirmation(self, verdict: Verdict) -> bool:
        limit = AUTOPILOT_MAX_RISK[self.cfg.autopilot]
        return limit is None or verdict.risk > limit

    def _process_command(self, reply: WaifuResponse) -> str:
        """Check, confirm and run the reply's command. Returns a [SYSTEM REPORT]."""
        command = reply.terminal_command or ""
        verdict = analyze(command, self.cfg.working_dir)

        if verdict.blocked:
            self._audit("BLOCKED", command, verdict)
            self.listener.on_blocked(command, verdict)
            return ("[SYSTEM REPORT] The safety guard BLOCKED this command and it did NOT run: "
                    f"`{command}`. Reason: {'; '.join(verdict.reasons)}. Tell the user you "
                    "won't do that and why, and do not suggest another way to do the same thing.")

        proposal = ProposedCommand(command, verdict, reply.dialogue)
        if self._needs_confirmation(verdict):
            if not self.listener.confirm(proposal):
                self._audit("DECLINED", command, verdict)
                return (f"[SYSTEM REPORT] The user declined to run `{command}`. It did not run. "
                        "Acknowledge it briefly; do not propose it again unless asked.")
            self._audit("APPROVED", command, verdict)
        else:
            self._audit("AUTOPILOT", command, verdict)

        self.listener.on_command_start(command)
        try:
            result = self.executor.run(command, password_provider=self.listener.sudo_password,
                                       on_output=self.listener.on_output)
        except CommandBlocked as exc:  # executor's own re-check; should not happen
            self._audit("BLOCKED", command, exc.verdict)
            self.listener.on_blocked(command, exc.verdict)
            return f"[SYSTEM REPORT] The safety guard BLOCKED `{command}`; it did NOT run."
        except OSError as exc:
            self.listener.on_error(f"Could not start the command: {exc}")
            return f"[SYSTEM REPORT] `{command}` could not be started: {exc}"

        self.listener.on_command_done(result)
        self._audit("RAN", command, verdict, exit_code=result.exit_code)
        return self._result_report(result)

    def _result_report(self, r: ExecResult) -> str:
        limit = self.cfg.output_chars_for_llm

        def tail(s: str) -> str:
            s = s.strip()
            return s if len(s) <= limit else "...(earlier output cut)...\n" + s[-limit:]

        status = ("TIMED OUT and was stopped" if r.timed_out else
                  "was CANCELLED" if r.cancelled else
                  f"finished with exit code {r.exit_code} ({'success' if r.ok else 'failure'})")
        return (f"[SYSTEM REPORT] Command `{r.command}` {status} after {r.duration:.1f}s.\n"
                f"STDOUT:\n{tail(r.stdout) or '(empty)'}\n"
                f"STDERR:\n{tail(r.stderr) or '(empty)'}")

    def _audit(self, action: str, command: str, verdict: Verdict, **extra) -> None:
        entry = {"time": dt.datetime.now().isoformat(timespec="seconds"), "action": action,
                 "command": command, "risk": verdict.risk.name, "reasons": verdict.reasons,
                 **extra}
        try:
            os.makedirs(os.path.dirname(self.cfg.audit_log), exist_ok=True)
            with open(self.cfg.audit_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass
