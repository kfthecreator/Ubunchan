# Ubunchan — Waifu Linux Desktop Assistant

A desktop app for Ubuntu: a 3D VRM avatar you can talk to (or type to). She answers out loud
with lip-sync and facial expressions, and can run terminal commands for you after you approve
them. A hard-coded safety guard makes sure she can **never** run `sudo rm -rf /*` or anything
like it.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m piper.download_voices --download-dir models/piper en_US-hfc_female-medium
cp config.example.toml config.toml   # optional
```

- **Claude backend:** set `ANTHROPIC_API_KEY` (or run `ant auth login`).
- **Local backend:** `ollama serve` with a pulled model (default `llama3.2:3b`). If no Claude key
  is found, the app falls back to Ollama automatically.
- The Whisper speech model (`base.en`, ~150 MB) is downloaded on first launch.
- **Avatar:** bring your own VRM model (make one for free in [VRoid Studio](https://vroid.com/en/studio)
  and export it as `.vrm`). Point the app at it with `--vrm path/to/model.vrm`, or set
  `vrm_path` in `config.toml`. VRM 0.x and 1.0 both work.

## Run

```bash
./scripts/ubunchan                         # desktop app
./scripts/ubunchan --backend ollama --theme light --vrm ~/models/other.vrm
./scripts/install-desktop-entry.sh         # add "Ubunchan" to the app menu
.venv/bin/python -m waifu --cli            # old terminal chat
.venv/bin/python -m waifu --check "sudo rm -rf /*"   # test the safety guard
.venv/bin/python -m pytest                 # tests
```

**Controls:** Enter sends · 🎙 or **Ctrl+M** talks (stops by itself when you finish) ·
**Esc** stops her talking · **Terminal** shows command output · the autopilot menu picks which
commands may run without asking · type `/reset` to clear the conversation.

## How it works

```
 mic ─ parecord ─ Whisper (STT) ─┐
 keyboard ───────────────────────┴─> Assistant ─> LLM (Claude / Ollama) ─> {dialogue, emotion, terminal_command}
                                        │                                         │
                                        │   safety.analyze ─ BLOCKED ─> never runs, reported back
                                        │        └ confirm dialog ─> Executor (sudo askpass) ─> output fed back to LLM
                                        └─> Piper (TTS) ─> WAV ─> avatar page: WebAudio plays it + drives mouth shapes
```

| Path | Role |
|---|---|
| `waifu/safety.py` | Classifies every command: READ_ONLY / NORMAL / DANGEROUS / BLOCKED |
| `waifu/executor.py` | Runs approved commands (re-checks safety, sudo via askpass, timeout, cancel) |
| `waifu/assistant.py` | LLM → safety → confirm → run → result back to LLM; audit log |
| `waifu/llm/` | `claude.py` (Anthropic structured outputs), `ollama.py` (local) |
| `waifu/voice/` | `tts.py` (Piper), `stt.py` (PipeWire recording + faster-whisper) |
| `waifu/gui/window.py` | Main window, confirm dialog, bottom bar, terminal drawer |
| `waifu/gui/workers.py` | Background threads for the assistant, TTS and STT |
| `waifu/gui/web/avatar.js` | three.js + three-vrm: idle animation, emotions, lip-sync |
| `waifu/gui/server.py` | localhost-only server for the page, the `.vrm` and the audio clips |

The avatar page can be opened in a normal browser for debugging:
`.venv/bin/python -m waifu.gui.server ~/Downloads/Ubunchan.vrm 8765` → http://127.0.0.1:8765

## Safety model

- **`rm -rf /*` is permanently banned**, and so are its variants (`sudo`, `bash -c`, `xargs`,
  `find -delete`, `cd / && rm -rf *`, `$HOME`, `--no-preserve-root`, inline Python...). Also
  banned: formatting disks, `dd` onto devices, fork bombs, `chmod -R /`, removing essential
  packages, and piping downloads into a shell. BLOCKED commands never run, whatever the config or
  confirmation says.
- Two independent layers: a shell parser plus a raw regex net. If a command can't be parsed, it's
  blocked. The executor re-checks every command itself.
- DANGEROUS commands always need you to type `yes`, even with autopilot on.
- The sudo password is asked for in a dialog, never goes to the LLM, and is stored only in a 0600
  file in `$XDG_RUNTIME_DIR` while the command runs.
- Every command is logged to `~/.local/share/waifu/audit.log`.
