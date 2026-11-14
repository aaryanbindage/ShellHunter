# Shell Hunter

See every shell Claude Code is running: its PID, which session it belongs to, and what it's doing right now.

A live local dashboard (`http://127.0.0.1:8765/`) shows, per Claude session:

- session name, status (busy/idle), Claude PID, session ID and working folder
- each shell's PID, uptime, memory, and whether it's running a command or idle
- the command it's running, and Claude's last tool call
- a Kill button for stuck shells

## Install

macOS / Linux / Git Bash:

```sh
bash install.sh      # venv + psutil + `shell-hunter` command on your PATH
shell-hunter
```

Windows (no installer needed):

```
pip install psutil
shell-hunter.cmd
```

Options: `--once` prints a text snapshot, `--port N` changes the port, `--no-browser` skips opening a tab.

## How it works

Live Claude instances are read from `~/.claude/sessions/<pid>.json`. Shells are the processes under each Claude PID that either carry `CLAUDECODE=1` in their environment or are named like a shell, collapsed to the topmost one per branch.
