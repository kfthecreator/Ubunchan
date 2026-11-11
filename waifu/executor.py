"""Runs approved shell commands and captures their output.

The executor re-runs the safety check itself, so even code that calls it
directly (bypassing the Assistant) can never run a BLOCKED command.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .safety import Verdict, analyze


class CommandBlocked(Exception):
    def __init__(self, command: str, verdict: Verdict):
        super().__init__(f"blocked: {command!r}: {'; '.join(verdict.reasons)}")
        self.command = command
        self.verdict = verdict


@dataclass
class ExecResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled


OutputCallback = Callable[[str, str], None]  # (line, "stdout" | "stderr")
PasswordProvider = Callable[[], "str | None"]

_SUDO_ENV = "DEBIAN_FRONTEND,NEEDRESTART_MODE,APT_LISTCHANGES_FRONTEND"


def _uses_sudo(command: str) -> bool:
    import re
    return re.search(r"(^|[\s;&|(])sudo(\s|$)", command) is not None


class Executor:
    def __init__(self, working_dir: str | None = None, timeout: int = 900):
        self.working_dir = working_dir or os.path.expanduser("~")
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._cancelled = False

    # -- public API -----------------------------------------------------

    def run(self, command: str, *, password_provider: PasswordProvider | None = None,
            on_output: OutputCallback | None = None) -> ExecResult:
        verdict = analyze(command, self.working_dir)
        if verdict.blocked:
            raise CommandBlocked(command, verdict)

        self._cancelled = False
        env = os.environ.copy()
        env.update({
            "DEBIAN_FRONTEND": "noninteractive",
            "NEEDRESTART_MODE": "a",  # don't open needrestart's interactive dialog
            "APT_LISTCHANGES_FRONTEND": "none",
            "LC_ALL": "C.UTF-8",  # predictable English output for the LLM
            "PAGER": "cat", "SYSTEMD_PAGER": "", "GIT_PAGER": "cat",
        })

        askpass_dir = None
        script = command
        try:
            if _uses_sudo(command):
                prep = self._prepare_sudo(env, password_provider)
                if isinstance(prep, ExecResult):
                    prep.command = command
                    return prep
                askpass_dir = prep
                # Route every `sudo` in the command through our askpass helper
                # and keep the non-interactive apt settings under sudo.
                prefix = (f'sudo() {{ command sudo -A --preserve-env={_SUDO_ENV} "$@"; }}; '
                          if askpass_dir else
                          f'sudo() {{ command sudo -n --preserve-env={_SUDO_ENV} "$@"; }}; ')
                script = prefix + "\n" + command
            return self._spawn(command, script, env, on_output)
        finally:
            if askpass_dir:
                shutil.rmtree(askpass_dir, ignore_errors=True)

    def cancel(self) -> None:
        """Stop the running command (and all its children)."""
        self._cancelled = True
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    # -- internals ------------------------------------------------------

    def _prepare_sudo(self, env: dict, password_provider: PasswordProvider | None):
        """Returns None (no password needed), an askpass dir, or an ExecResult error."""
        if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0:
            return None
        if password_provider is None:
            return ExecResult("", 1, "", "sudo needs a password but none can be asked for", 0.0)
        password = password_provider()
        if not password:
            return ExecResult("", 1, "", "sudo password was not provided", 0.0, cancelled=True)

        # The password lives only in a 0600 file inside a 0700 directory on
        # the per-user tmpfs, and is deleted as soon as the command ends.
        base = os.environ.get("XDG_RUNTIME_DIR") or None
        askpass_dir = tempfile.mkdtemp(prefix="waifu-askpass-", dir=base)
        os.chmod(askpass_dir, 0o700)
        pw_file = os.path.join(askpass_dir, "pw")
        fd = os.open(pw_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(password + "\n")
        helper = os.path.join(askpass_dir, "askpass.sh")
        with open(helper, "w") as f:
            f.write(f"#!/bin/sh\ncat '{pw_file}'\n")
        os.chmod(helper, 0o700)
        env["SUDO_ASKPASS"] = helper
        del password

        check = subprocess.run(["sudo", "-A", "-v"], env=env, capture_output=True, text=True)
        if check.returncode != 0:
            shutil.rmtree(askpass_dir, ignore_errors=True)
            return ExecResult("", 1, "", "sudo rejected the password (wrong password?)", 0.0)
        return askpass_dir

    def _spawn(self, command: str, script: str, env: dict,
               on_output: OutputCallback | None) -> ExecResult:
        start = time.monotonic()
        proc = subprocess.Popen(
            ["bash", "-c", script],
            cwd=self.working_dir,
            env=env,
            stdin=subprocess.DEVNULL,  # nothing can wait for keyboard input
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            bufsize=1,
            start_new_session=True,  # own process group, so cancel kills children
        )
        self._proc = proc
        out: list[str] = []
        err: list[str] = []

        def pump(stream, sink, name):
            for line in stream:
                sink.append(line)
                if on_output:
                    on_output(line.rstrip("\n"), name)

        threads = [threading.Thread(target=pump, args=(proc.stdout, out, "stdout"), daemon=True),
                   threading.Thread(target=pump, args=(proc.stderr, err, "stderr"), daemon=True)]
        for t in threads:
            t.start()

        timed_out = False
        try:
            proc.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        for t in threads:
            t.join(timeout=5)
        self._proc = None

        return ExecResult(
            command=command,
            exit_code=proc.returncode,
            stdout="".join(out),
            stderr="".join(err),
            duration=time.monotonic() - start,
            timed_out=timed_out,
            cancelled=self._cancelled,
        )
