"""Text REPL for Phase 1: chat with the assistant and let it run commands.

    python -m waifu --cli                    # chat (Claude by default)
    python -m waifu --cli --backend ollama   # local model
    python -m waifu --check "sudo rm -rf /*" # just run the safety check
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from .assistant import Assistant, Listener, ProposedCommand
from .config import load_config
from .executor import ExecResult
from .llm import create_backend
from .persona import build_system_prompt
from .safety import Risk, Verdict, analyze
from .schema import WaifuResponse

USE_COLOR = sys.stdout.isatty()


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


RISK_COLOR = {Risk.READ_ONLY: "32", Risk.NORMAL: "36", Risk.DANGEROUS: "33", Risk.BLOCKED: "31;1"}


class CliListener(Listener):
    def __init__(self, name: str):
        self.name = name

    def on_reply(self, reply: WaifuResponse) -> None:
        print(f"{c(self.name, '35;1')} {c('[' + reply.emotion + ']', '2')}: {reply.dialogue}")

    def on_blocked(self, command: str, verdict: Verdict) -> None:
        print(c(f"  ⛔ BLOCKED: {command}", "31;1"))
        for r in verdict.reasons:
            print(c(f"     - {r}", "31"))

    def confirm(self, proposal: ProposedCommand) -> bool:
        v = proposal.verdict
        print(f"  I'm going to run: {c(proposal.command, '1')}")
        print(f"  risk: {c(v.risk.name, RISK_COLOR[v.risk])}"
              + (f"  ({'; '.join(v.reasons)})" if v.reasons else ""))
        try:
            if v.risk is Risk.DANGEROUS:
                answer = input(c("  This is DANGEROUS. Type 'yes' to proceed: ", "33"))
                return answer.strip().lower() == "yes"
            answer = input("  Should I proceed? [y/N] ")
            return answer.strip().lower() in ("y", "yes")
        except EOFError:
            return False

    def sudo_password(self) -> str | None:
        try:
            return getpass.getpass(c("  [sudo] password for " + getpass.getuser() + ": ", "33"))
        except (EOFError, KeyboardInterrupt):
            return None

    def on_command_start(self, command: str) -> None:
        print(c(f"  $ {command}", "2"))

    def on_output(self, line: str, stream: str) -> None:
        print(c("  │ ", "2") + (c(line, "31") if stream == "stderr" else line))

    def on_command_done(self, result: ExecResult) -> None:
        mark = c("✔", "32") if result.ok else c("✘", "31")
        print(c(f"  {mark} exit {result.exit_code} in {result.duration:.1f}s", "2"))

    def on_error(self, message: str) -> None:
        print(c(f"  ! {message}", "31"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="waifu", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="path to config.toml")
    ap.add_argument("--backend", choices=["claude", "ollama"])
    ap.add_argument("--model", help="override the model for the chosen backend")
    ap.add_argument("--autopilot", choices=["off", "readonly", "normal"])
    ap.add_argument("--check", metavar="CMD", help="only run the safety check on CMD")
    args = ap.parse_args(argv)

    if args.check is not None:
        v = analyze(args.check)
        print(f"{c(v.risk.name, RISK_COLOR[v.risk])}: {args.check}")
        for r in v.reasons:
            print(f"  - {r}")
        return 1 if v.blocked else 0

    cfg = load_config(args.config)
    if args.backend:
        cfg.backend = args.backend
    if args.model:
        getattr(cfg, cfg.backend).model = args.model
    if args.autopilot:
        cfg.autopilot = args.autopilot

    if os.geteuid() == 0:
        print(c("Please don't run the assistant as root; it uses sudo when needed.", "31"))
        return 1

    listener = CliListener(cfg.assistant_name)
    llm = create_backend(cfg, build_system_prompt(cfg.assistant_name))
    assistant = Assistant(cfg, llm, listener=listener)

    model = getattr(cfg, cfg.backend).model
    print(c(f"{cfg.assistant_name} is here! (backend: {cfg.backend}/{model}, "
            f"autopilot: {cfg.autopilot})  Type /quit to exit, /reset to forget.", "2"))
    while True:
        try:
            text = input(c("you: ", "34;1")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text in ("/quit", "/exit"):
            return 0
        if text == "/reset":
            llm.reset()
            print(c("  (conversation forgotten)", "2"))
            continue
        try:
            assistant.handle(text)
        except KeyboardInterrupt:
            assistant.executor.cancel()
            print(c("\n  (interrupted)", "33"))


if __name__ == "__main__":
    sys.exit(main())
