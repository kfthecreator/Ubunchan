"""Command safety analysis.

Every command the LLM proposes goes through ``analyze()`` before it is shown
to the user, and again inside the executor right before it runs. There is no
flag, config option or confirmation that lets a BLOCKED command run.

The single most important rule (requested by the owner of this machine):
    the assistant must NEVER run ``sudo rm -rf /*`` or anything equivalent.

We enforce that with two independent layers:
  1. A structural parser that splits the command into pipelines, strips
     wrappers (sudo, env, xargs, bash -c, ...) and inspects each real program
     and its arguments.
  2. A raw regex safety net over the whole string (including quoted text), so
     things like ``python3 -c "os.system('rm -rf /')"`` are still caught.

If the command cannot be parsed, it is blocked. When in doubt, block.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from enum import IntEnum


class Risk(IntEnum):
    READ_ONLY = 0  # only reads state (ls, df, apt list ...)
    NORMAL = 1  # changes state in an ordinary way (apt install, mkdir ...)
    DANGEROUS = 2  # destructive or hard to undo; always needs confirmation
    BLOCKED = 3  # never runs, not even with confirmation


@dataclass
class Verdict:
    risk: Risk
    reasons: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.risk is Risk.BLOCKED

    def raise_to(self, risk: Risk, reason: str) -> None:
        if risk > self.risk:
            self.risk = risk
        if reason not in self.reasons:
            self.reasons.append(reason)


# --------------------------------------------------------------------------
# Protected filesystem locations
# --------------------------------------------------------------------------

HOME = os.path.expanduser("~")

# Deleting / moving / chmod -R'ing any of these (or a glob inside them) is
# blocked. Every top-level directory ("/x") is also protected automatically.
PROTECTED_PATHS = {
    "/",
    HOME,
    os.path.dirname(HOME) or "/",
    "/usr/bin", "/usr/sbin", "/usr/lib", "/usr/lib32", "/usr/lib64",
    "/usr/libexec", "/usr/include", "/usr/share", "/usr/local", "/usr/src",
    "/var/lib", "/var/lib/dpkg", "/var/lib/apt", "/var/cache", "/var/log",
    "/boot/efi", "/etc/apt", "/etc/ssh", "/etc/systemd", "/etc/default",
    "/snap/bin",
}

# Packages whose removal can leave the system unbootable or unmanageable.
CRITICAL_PACKAGES = re.compile(
    r"^(sudo|systemd|systemd-sysv|libc6|libc-bin|apt|dpkg|coreutils|bash|"
    r"dash|login|passwd|util-linux|base-files|base-passwd|init|"
    r"linux-image-.*|linux-generic.*|grub.*|shim-signed|ubuntu-minimal|"
    r"network-manager|netplan\.io|python3|python3-minimal|perl-base|"
    r"gnome-shell|gdm3|ubuntu-desktop|ubuntu-desktop-minimal|\*.*)$"
)

GLOB_CHARS = set("*?[")

# Wrappers that just run another command: we strip them and inspect the rest.
# Value = number of positional arguments the wrapper consumes itself.
WRAPPERS = {
    "sudo": 0, "doas": 0, "pkexec": 0, "env": 0, "nohup": 0, "nice": 0,
    "ionice": 0, "time": 0, "command": 0, "builtin": 0, "exec": 0,
    "stdbuf": 0, "chrt": 1, "taskset": 1, "timeout": 1, "xargs": 0,
    "unbuffer": 0, "setsid": 0, "busybox": 0, "flock": 1, "watch": 0,
}
# Options of wrappers that take a separate value argument.
WRAPPER_OPTS_WITH_VALUE = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T",
             "--user", "--group", "--prompt", "--chdir"},
    "doas": {"-u", "-C"},
    "pkexec": {"--user"},
    "env": {"-u", "-C", "--unset", "--chdir"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "--class", "--classdata"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "xargs": {"-a", "-d", "-E", "-e", "-I", "-i", "-L", "-l", "-n", "-P",
              "-s", "--arg-file", "--delimiter", "--max-args",
              "--max-procs", "--replace"},
    "stdbuf": {"-i", "-o", "-e"},
    "watch": {"-n", "-d", "--interval"},
}

SHELLS = {"sh", "bash", "dash", "zsh", "ksh", "fish", "csh", "tcsh", "mksh"}
INTERPRETERS = {"python", "python2", "python3", "perl", "ruby", "node",
                "nodejs", "php", "lua", "awk", "gawk", "mawk"}
INTERACTIVE = {"vi", "vim", "nvim", "nano", "emacs", "less", "more", "top",
               "htop", "btop", "man", "ssh", "ftp", "telnet", "mysql",
               "psql", "sqlite3", "tmux", "screen", "su", "passwd",
               "visudo", "vipw"}

READ_ONLY_CMDS = {
    "ls", "ll", "cat", "head", "tail", "grep", "egrep", "fgrep", "rg",
    "find", "locate", "which", "whereis", "type", "file", "stat", "wc",
    "df", "du", "free", "uptime", "uname", "hostname", "hostnamectl",
    "whoami", "id", "groups", "who", "w", "last", "date", "cal", "pwd",
    "echo", "printf", "env", "printenv", "lsblk", "lscpu", "lspci", "lsusb",
    "lsmod", "lshw", "dmidecode", "ip", "ifconfig", "ss", "netstat",
    "ping", "dig", "nslookup", "host", "ps", "pgrep", "pstree", "sort",
    "uniq", "cut", "tr", "column", "diff", "cmp", "md5sum", "sha256sum",
    "lsb_release", "neofetch", "fastfetch", "journalctl", "dmesg",
    "sensors", "nvidia-smi", "xrandr", "true", "false", "test", "[",
    "basename", "dirname", "realpath", "readlink", "tree", "jq", "awk",
    "sed", "nproc", "arch", "getent", "apt-cache", "dpkg-query", "policy",
    "flatpak-list", "ufw-status", "timedatectl", "localectl", "loginctl",
    "systemd-analyze", "inxi", "upower", "acpi", "lsof", "fc-list",
}

# Sub-commands that make an otherwise state-changing tool read-only.
READ_ONLY_SUBCOMMANDS = {
    "apt": {"list", "search", "show", "policy", "depends", "rdepends",
            "changelog"},
    "apt-get": {"check", "changelog"},
    "dpkg": {"-l", "-L", "-s", "-S", "--list", "--listfiles", "--status",
             "--search", "--get-selections", "--print-architecture"},
    "snap": {"list", "find", "info", "version", "services", "changes"},
    "flatpak": {"list", "search", "info", "remotes", "history"},
    "systemctl": {"status", "list-units", "list-unit-files", "is-active",
                  "is-enabled", "is-failed", "show", "cat", "list-timers",
                  "--failed", "list-dependencies"},
    "ufw": {"status"},
    "nmcli": {"device", "connection", "general", "radio", "monitor"},
    "git": {"status", "log", "diff", "show", "branch", "remote"},
    "docker": {"ps", "images", "info", "version", "logs", "inspect"},
    "pip": {"list", "show", "freeze"},
    "pip3": {"list", "show", "freeze"},
    "ubuntu-drivers": {"devices", "list"},
    "fwupdmgr": {"get-devices", "get-updates"},
    "gsettings": {"get", "list-keys", "list-schemas", "list-recursively"},
}

DANGEROUS_CMDS = {
    "shutdown", "reboot", "poweroff", "halt", "init", "telinit", "kill",
    "killall", "pkill", "fdisk", "gdisk", "sgdisk", "parted", "gparted",
    "cfdisk", "sfdisk", "mkswap", "swapoff", "cryptsetup", "lvremove",
    "vgremove", "pvremove", "mdadm", "userdel", "deluser", "groupdel",
    "usermod", "chpasswd", "iptables", "ip6tables", "nft", "update-grub",
    "grub-install", "efibootmgr", "crontab", "truncate", "dd", "rm",
    "rmdir", "unlink", "shred", "mv", "chmod", "chown", "chgrp", "setfacl",
    "ln", "mount", "umount", "modprobe", "rmmod", "insmod", "sysctl",
    "do-release-upgrade", "update-alternatives", "dpkg-reconfigure",
}

ALWAYS_BLOCKED_CMDS = {
    "mkfs", "wipefs", "blkdiscard", "mke2fs", "mkntfs", "mkdosfs",
    "zerofree", "eval", "source", ".",
}

# --------------------------------------------------------------------------
# Raw regex safety net (applied to the whole string, quotes included)
# --------------------------------------------------------------------------

_RM = r"(?:^|[\s;&|(`'\"$]|/)rm\s"
_TARGET_ROOTISH = (
    r"(?:\s|['\"]|--\s+)"  # separator before the target
    r"(?:"
    r"/+\**(?:\.)?/*"  # /  //  /*  /.  /./
    r"|/+(?:bin|boot|dev|etc|home|lib\w*|media|mnt|opt|proc|root|run|"
    r"sbin|snap|srv|sys|usr|var)/*\**"
    r"|~/*\**|\$\{?HOME\}?/*\**"
    r")"
    r"(?=$|[\s;&|)`'\"])"
)

RAW_BLOCK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(_RM + r".*" + _TARGET_ROOTISH),
     "rm targeting the root filesystem, a system directory or your home "
     "directory is permanently banned"),
    (re.compile(r"--no-preserve-root"),
     "--no-preserve-root is permanently banned"),
    (re.compile(r"\brmtree\s*\(\s*['\"](?:/|~|/home|/usr|/etc)['\"/]"),
     "recursive delete of a system path from inline code"),
    (re.compile(r":\s*\(\s*\)\s*\{.*:\s*\|\s*:.*&.*\}"), "fork bomb"),
    (re.compile(r"\bmkfs(\.\w+)?\b"), "formatting a filesystem"),
    (re.compile(r"\bof\s*=\s*/dev/(sd|hd|vd|xvd|nvme|mmcblk|dm-|md|loop|disk)"),
     "writing raw data onto a disk device"),
    (re.compile(r">\s*/dev/(sd|hd|vd|xvd|nvme|mmcblk|dm-|md)"),
     "redirecting output onto a disk device"),
    (re.compile(r"\b(shred|wipefs|blkdiscard)\b.*?/dev/"),
     "wiping a disk device"),
    (re.compile(r"(?<![>])>\s*/etc/(passwd|shadow|group|gshadow|sudoers|fstab)\b"),
     "overwriting a critical system file"),
    (re.compile(r"\bchmod\s+(-\w*\s+)*-?\w*R\w*\s+\S*\s+/+(\s|$|\*)"),
     "recursive chmod on /"),
    (re.compile(r"\bchown\s+(-\w*\s+)*-?\w*R\w*\s+\S*\s+/+(\s|$|\*)"),
     "recursive chown on /"),
    (re.compile(r"\bmv\s+(-\S+\s+)*/+\*?\s"), "moving the root filesystem"),
    (re.compile(r"\bbase64\b.*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b"),
     "obfuscated script piped into a shell"),
]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _resolve(path: str, cwd: str) -> str | None:
    """Resolve a path argument; None if it depends on runtime expansion."""
    if path.startswith("~"):
        path = HOME + path[1:] if path == "~" or path.startswith("~/") else path
    path = path.replace("${HOME}", HOME).replace("$HOME", HOME)
    if "$" in path or "`" in path:
        return None
    if not path.startswith("/"):
        path = os.path.join(cwd, path)
    return os.path.normpath(path).replace("//", "/") or "/"


def _is_protected(resolved: str) -> bool:
    if resolved in PROTECTED_PATHS:
        return True
    parts = [p for p in resolved.split("/") if p]
    return len(parts) <= 1  # "/" itself or any top-level directory like /usr


def _path_is_dangerous(arg: str, cwd: str) -> str | None:
    """Return a reason if deleting/moving ``arg`` would hit a protected path."""
    resolved = _resolve(arg, cwd)
    if resolved is None:
        return f"path '{arg}' depends on a variable or subcommand, cannot verify it"
    if _is_protected(resolved):
        return f"'{arg}' is a protected system location ({resolved})"
    if any(c in resolved for c in GLOB_CHARS):
        # Find the directory part before the first glob character.
        idx = min(resolved.index(c) for c in GLOB_CHARS if c in resolved)
        base = resolved[:idx].rsplit("/", 1)[0] or "/"
        if _is_protected(base):
            return f"wildcard '{arg}' would match everything in protected {base}"
    return None


def _split_flags(args: list[str]) -> tuple[set[str], list[str]]:
    """Separate option flags from positional args (honouring '--')."""
    flags: set[str] = set()
    positional: list[str] = []
    end_of_opts = False
    for a in args:
        if end_of_opts or not a.startswith("-") or a == "-":
            positional.append(a)
        elif a == "--":
            end_of_opts = True
        elif a.startswith("--"):
            flags.add(a.split("=", 1)[0])
        else:
            flags.update("-" + ch for ch in a[1:])
    return flags, positional


def _strip_wrappers(argv: list[str], verdict: Verdict) -> list[str]:
    """Remove sudo/env/xargs/... and VAR=value prefixes; return real argv."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        name = os.path.basename(tok)
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            i += 1
            continue
        if name not in WRAPPERS:
            break
        if name in ("sudo", "doas", "pkexec", "su"):
            if any(a in ("-i", "-s", "--login", "--shell") for a in argv[i + 1:i + 3]) \
                    or i + 1 >= len(argv):
                verdict.raise_to(Risk.BLOCKED, "opening an interactive root shell is not allowed")
        if name == "busybox":
            i += 1
            break
        i += 1
        takes_value = WRAPPER_OPTS_WITH_VALUE.get(name, set())
        while i < len(argv) and argv[i].startswith("-") and argv[i] != "--":
            opt = argv[i]
            i += 1
            if opt in takes_value and "=" not in opt:
                i += 1
        if i < len(argv) and argv[i] == "--":
            i += 1
        # Positional args that belong to the wrapper itself (e.g. timeout 10).
        i += WRAPPERS[name]
        # env: skip VAR=value assignments
        while i < len(argv) and name == "env" and "=" in argv[i]:
            i += 1
    return argv[i:]


# --------------------------------------------------------------------------
# Tokenising
# --------------------------------------------------------------------------

_SUBST_RE = re.compile(r"\$\(([^()]*(?:\([^()]*\)[^()]*)*)\)|`([^`]*)`")
_OPERATORS = {";", "&&", "||", "|", "&", "\n", "(", ")", "|&", ";;"}
_REDIRECTS = {">", ">>", "<", "<<", "<<<", ">&", "&>", "&>>", "<>", ">|"}


def _tokenize(command: str) -> list[str]:
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def _segments(tokens: list[str]) -> list[tuple[list[str], list[tuple[str, str]], str]]:
    """Split tokens into simple commands.

    Returns (argv, redirects, operator_before) for each simple command.
    """
    result = []
    argv: list[str] = []
    redirs: list[tuple[str, str]] = []
    prev_op = ""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _OPERATORS:
            if argv or redirs:
                result.append((argv, redirs, prev_op))
            argv, redirs, prev_op = [], [], tok
        elif tok in _REDIRECTS or re.fullmatch(r"\d?[<>]+&?", tok):
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            # strip fd prefixes like "2" in "2>" that shlex left on argv
            if argv and argv[-1].isdigit():
                argv.pop()
            redirs.append((tok, target))
            i += 1
        else:
            argv.append(tok)
        i += 1
    if argv or redirs:
        result.append((argv, redirs, prev_op))
    return result


# --------------------------------------------------------------------------
# Per-program rules
# --------------------------------------------------------------------------

def _check_program(argv: list[str], cwd: str, verdict: Verdict, depth: int,
                   piped_into: bool) -> str:
    """Inspect one simple command. Returns the new cwd (for `cd`)."""
    real = _strip_wrappers(argv, verdict)
    if not real:
        return cwd
    prog = os.path.basename(real[0])
    args = real[1:]
    flags, pos = _split_flags(args)

    wrappers_used = {os.path.basename(a) for a in argv[:len(argv) - len(real)]}
    if "xargs" in wrappers_used and prog in (
            "rm", "unlink", "rmdir", "shred", "mv", "chmod", "chown", "chgrp", "dd"):
        verdict.raise_to(Risk.BLOCKED,
                         f"xargs {prog} takes its targets from input, so they cannot be verified")
        return cwd

    if prog.startswith("mkfs") or prog in ALWAYS_BLOCKED_CMDS:
        verdict.raise_to(Risk.BLOCKED, f"'{prog}' is never allowed")
        return cwd

    if prog in INTERACTIVE:
        verdict.raise_to(Risk.BLOCKED, f"'{prog}' is interactive and would hang the assistant")
        return cwd

    if prog == "cd":
        target = pos[0] if pos else "~"
        new = _resolve(target, cwd)
        return new if new else cwd

    if prog in SHELLS:
        if "-c" in flags or any(a == "-c" for a in args):
            idx = args.index("-c") if "-c" in args else next(
                i for i, a in enumerate(args) if a.startswith("-") and "c" in a)
            inner = args[idx + 1] if idx + 1 < len(args) else ""
            _analyze_into(inner, cwd, verdict, depth + 1)
            return cwd
        if piped_into:
            verdict.raise_to(Risk.BLOCKED, "piping data into a shell hides what actually runs")
        elif not pos:
            verdict.raise_to(Risk.BLOCKED, "opening an interactive shell is not allowed")
        else:
            verdict.raise_to(Risk.DANGEROUS, f"runs the script '{pos[0]}' whose contents are not checked")
        return cwd

    if prog in INTERPRETERS or re.match(r"^python3?\.\d+$", prog):
        if piped_into and prog not in ("awk", "gawk", "mawk"):
            verdict.raise_to(Risk.BLOCKED, f"piping data into {prog} hides what actually runs")
        elif prog not in ("awk", "gawk", "mawk"):
            verdict.raise_to(Risk.DANGEROUS, f"runs {prog} code that is not fully checked")
        return cwd

    # --- deletion -------------------------------------------------------
    if prog in ("rm", "unlink", "rmdir", "shred", "srm"):
        recursive = bool(flags & {"-r", "-R", "--recursive"})
        if "--no-preserve-root" in flags:
            verdict.raise_to(Risk.BLOCKED, "--no-preserve-root is permanently banned")
        for p in pos:
            reason = _path_is_dangerous(p, cwd)
            if reason:
                verdict.raise_to(Risk.BLOCKED, f"refusing to delete: {reason}")
        if prog == "rm" and recursive and any(c in "".join(pos) for c in GLOB_CHARS):
            verdict.raise_to(Risk.DANGEROUS, "recursive delete with a wildcard")
        verdict.raise_to(Risk.DANGEROUS, "deletes files")
        return cwd

    if prog == "find":
        start = [a for a in args if not a.startswith("-")][:1] or ["."]
        destructive = "-delete" in args or any(
            a in ("-exec", "-execdir", "-ok") and i + 1 < len(args)
            and os.path.basename(args[i + 1]) in ("rm", "shred", "unlink", "mv", "chmod", "chown")
            for i, a in enumerate(args))
        if destructive:
            reason = _path_is_dangerous(start[0], cwd)
            if reason:
                verdict.raise_to(Risk.BLOCKED, f"find with delete: {reason}")
            verdict.raise_to(Risk.DANGEROUS, "find that deletes or modifies files")
        else:
            verdict.raise_to(Risk.READ_ONLY, "")
        return cwd

    if prog == "rsync" and any(f.startswith("--delete") for f in flags):
        if pos and _path_is_dangerous(pos[-1], cwd):
            verdict.raise_to(Risk.BLOCKED, "rsync --delete into a protected location")
        verdict.raise_to(Risk.DANGEROUS, "rsync --delete removes files")
        return cwd

    if prog in ("mv",):
        for p in pos[:-1] if len(pos) > 1 else pos:
            reason = _path_is_dangerous(p, cwd)
            if reason:
                verdict.raise_to(Risk.BLOCKED, f"refusing to move: {reason}")
        if pos and pos[-1] == "/dev/null":
            verdict.raise_to(Risk.BLOCKED, "moving files into /dev/null destroys them")
        verdict.raise_to(Risk.DANGEROUS, "moves/renames files")
        return cwd

    if prog in ("chmod", "chown", "chgrp", "setfacl"):
        recursive = bool(flags & {"-R", "--recursive"})
        targets = pos[1:] if prog != "setfacl" else pos
        for p in targets:
            resolved = _resolve(p, cwd)
            if resolved is None or (recursive and _path_is_dangerous(p, cwd)) \
                    or resolved == "/":
                verdict.raise_to(Risk.BLOCKED, f"{prog} on protected location '{p}'")
        verdict.raise_to(Risk.DANGEROUS, f"{prog} changes permissions/ownership")
        return cwd

    if prog == "dd":
        for a in args:
            if a.startswith("of=/dev/") and not a.startswith("of=/dev/null"):
                verdict.raise_to(Risk.BLOCKED, "dd onto a device can wipe a disk")
        verdict.raise_to(Risk.DANGEROUS, "dd writes raw data")
        return cwd

    # --- packages -------------------------------------------------------
    if prog in ("apt", "apt-get", "aptitude", "nala"):
        sub = pos[0] if pos else ""
        if sub in READ_ONLY_SUBCOMMANDS.get(prog, set()) or sub in ("list", "search", "show", "policy"):
            verdict.raise_to(Risk.READ_ONLY, "")
        elif sub in ("remove", "purge", "autoremove", "autopurge"):
            for pkg in pos[1:]:
                if CRITICAL_PACKAGES.match(pkg.split(":")[0].split("=")[0]):
                    verdict.raise_to(Risk.BLOCKED, f"removing '{pkg}' could break your system")
            verdict.raise_to(Risk.DANGEROUS, "removes packages")
        elif sub in ("full-upgrade", "dist-upgrade"):
            verdict.raise_to(Risk.DANGEROUS, "full system upgrade may remove packages")
        else:
            verdict.raise_to(Risk.NORMAL, "")
        return cwd

    if prog in ("dpkg", "snap", "flatpak"):
        sub = pos[0] if pos else (args[0] if args else "")
        if sub in READ_ONLY_SUBCOMMANDS.get(prog, set()) or \
                (prog == "dpkg" and flags & {"-l", "-L", "-s", "-S"}):
            verdict.raise_to(Risk.READ_ONLY, "")
        elif prog == "dpkg" and flags & {"-r", "-P", "--remove", "--purge"}:
            for pkg in pos:
                if CRITICAL_PACKAGES.match(pkg):
                    verdict.raise_to(Risk.BLOCKED, f"removing '{pkg}' could break your system")
            verdict.raise_to(Risk.DANGEROUS, "removes packages")
        elif sub in ("remove", "uninstall"):
            verdict.raise_to(Risk.DANGEROUS, "removes packages")
        else:
            verdict.raise_to(Risk.NORMAL, "")
        return cwd

    # --- generic classification ----------------------------------------
    if prog in DANGEROUS_CMDS:
        verdict.raise_to(Risk.DANGEROUS, f"'{prog}' can be destructive")
        return cwd

    if prog in READ_ONLY_SUBCOMMANDS:
        sub = pos[0] if pos else (args[0] if args else "")
        if sub in READ_ONLY_SUBCOMMANDS[prog]:
            verdict.raise_to(Risk.READ_ONLY, "")
        elif prog == "systemctl" and sub in ("poweroff", "reboot", "halt", "kexec",
                                              "suspend", "hibernate", "mask",
                                              "isolate", "rescue", "emergency"):
            verdict.raise_to(Risk.DANGEROUS, f"systemctl {sub}")
        else:
            verdict.raise_to(Risk.NORMAL, "")
        return cwd

    if prog in READ_ONLY_CMDS:
        if prog == "sed" and (flags & {"-i"} or any(a.startswith("--in-place") for a in args)):
            verdict.raise_to(Risk.NORMAL, "")
        else:
            verdict.raise_to(Risk.READ_ONLY, "")
        return cwd

    verdict.raise_to(Risk.NORMAL, "")
    return cwd


def _analyze_into(command: str, cwd: str, verdict: Verdict, depth: int = 0) -> None:
    if depth > 5:
        verdict.raise_to(Risk.BLOCKED, "command is nested too deeply to verify")
        return

    # Raw safety net first — independent of parsing.
    for pattern, reason in RAW_BLOCK_PATTERNS:
        if pattern.search(command):
            verdict.raise_to(Risk.BLOCKED, reason)

    # Analyse command substitutions separately, then replace them with a
    # placeholder that path checks treat as "unresolvable".
    for m in _SUBST_RE.finditer(command):
        _analyze_into(m.group(1) or m.group(2) or "", cwd, verdict, depth + 1)
    flat = _SUBST_RE.sub("$SUBST", command)
    if "$(" in flat or "`" in flat:
        verdict.raise_to(Risk.BLOCKED, "command substitution too complex to verify")

    if "<<" in flat:
        verdict.raise_to(Risk.DANGEROUS, "here-documents are not fully checked")

    try:
        tokens = _tokenize(flat)
    except ValueError as exc:
        verdict.raise_to(Risk.BLOCKED, f"could not parse the command ({exc})")
        return

    for argv, redirs, op in _segments(tokens):
        for rop, target in redirs:
            if ">" in rop and target:
                resolved = _resolve(target, cwd)
                if resolved and resolved.startswith("/dev/") and resolved not in (
                        "/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"):
                    verdict.raise_to(Risk.BLOCKED, f"writing into device {resolved}")
                elif resolved is None or (resolved and _is_protected(resolved)):
                    verdict.raise_to(Risk.BLOCKED, f"redirecting output onto '{target}'")
                elif resolved and resolved.startswith(("/etc/", "/boot/", "/usr/")):
                    verdict.raise_to(Risk.DANGEROUS, f"overwrites system file {resolved}")
                else:
                    verdict.raise_to(Risk.NORMAL, "")
        if argv:
            cwd = _check_program(argv, cwd, verdict, depth, piped_into=op in ("|", "|&"))


def analyze(command: str | None, cwd: str | None = None) -> Verdict:
    """Classify a shell command. Never raises."""
    verdict = Verdict(Risk.READ_ONLY)
    if not command or not command.strip():
        verdict.raise_to(Risk.BLOCKED, "empty command")
        return verdict
    if "\x00" in command or len(command) > 4000:
        verdict.raise_to(Risk.BLOCKED, "command is malformed or too long to verify")
        return verdict
    try:
        _analyze_into(command, cwd or HOME, verdict)
    except Exception as exc:  # any parser bug => block, never allow
        verdict.raise_to(Risk.BLOCKED, f"safety check failed internally ({exc})")
    verdict.reasons = [r for r in verdict.reasons if r]
    return verdict
