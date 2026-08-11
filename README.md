# Flappy Codex

A lightweight, opt-in terminal companion for the existing Codex CLI. It does
not contain, patch, compile, or rebuild Codex.

The game has its own Codex-themed terminal presentation: a flying prompt cursor
(`/>_`, `=>_`, `\>_`), cyan `[=]` execution gates, static code particles, a
stable `>_..` prompt rail, a midnight synth palette, and a live difficulty
readout. Decorative elements remain still to avoid terminal shimmer; gates move
at a synchronized one-cell cadence. Gravity is deliberately severe, gaps start
narrow and tighten again, and execution speed increases every two points. Best
score persists between sessions, and the layout adapts cleanly to smaller
terminals.

## Install

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/flappycodex.git
cd flappycodex
./install.sh
```

Then open a new terminal:

```bash
codex --flappy
```

All normal arguments are forwarded:

```bash
codex --flappy --model gpt-5 "inspect this project"
```

The installer leaves the original Codex files alone and adds one small Python
launcher earlier on `PATH`. Plain `codex` immediately replaces that launcher
process with the next real Codex executable on your current `PATH` (using the
install-time path only as a fallback). Its arguments, stdin, stdout, signals,
prompts, and rendering stay on the original path. This also lets Node version
managers and Codex upgrades keep working normally.

## How it works

`--flappy` creates an isolated tmux game pane and runs the original Codex in the
main pane. Temporary per-session lifecycle hooks signal the game when Codex:

- starts or finishes work;
- requests user input;
- requests permission; or
- ends the session.

The hook configuration is passed only on the `--flappy` command line. The addon
does not edit `~/.codex/config.toml`, and it calculates the hook trust hashes
instead of bypassing Codex's hook security.

Click the lower pane (or use normal tmux pane navigation) and press Space to
start. Flap with Space, Up, or a mouse click.
Inside an existing tmux session, click-to-focus follows your existing mouse
setting; the addon does not change it. The addon never installs a global Space
binding and never reads from Codex's pane. When a supported Codex prompt hook
fires, focus returns to the Codex pane before the prompt is shown. After the
answer is accepted, the game displays a 3-second countdown.

For permission prompts, Codex currently exposes the pre-prompt hook and the
post-tool hook. The game therefore remains safely paused until that approved
tool finishes. If permission is denied or aborted, it remains paused until the
next lifecycle event (normally Codex becoming idle). This favors input safety
over guessing from terminal output.

Requirements: Linux or macOS, Python 3.10+, tmux, and a Codex CLI with
lifecycle hooks (tested with Codex 0.147.0). Without tmux, plain `codex` still
works and `codex --flappy` prints a clear error.

## Controls

- Space / Up / click: launch, boost, or reboot after a failed run
- R: return to the start screen
- Esc/Q: close only the game pane

## Uninstall

```bash
./uninstall.sh
```

The original Codex installation is never removed.
