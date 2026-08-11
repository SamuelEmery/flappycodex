#!/usr/bin/env python3
"""A tiny, opt-in Flappy companion for the unmodified Codex CLI."""

from __future__ import annotations

import curses
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Sequence

FLAPPY_CODEX_SHIM = 1
COUNTDOWN_SECONDS = 3.0
IDLE_SECONDS = 0.20
SESSION_HOOK_SOURCE = "/<session-flags>/config.toml"

# A wide terminal scene that scales down cleanly in a short tmux pane.
REFERENCE_WIDTH = 100
REFERENCE_SKY_HEIGHT = 27
BASE_GATE_SPEED = 20.0
MAX_GATE_SPEED = 28.0
GATE_SPACING = 30.0
SCORES_PER_LEVEL = 2
GRAVITY = 26.0
MAX_FALL_SPEED = 15.0
FLAP_VELOCITY = -8.6
BIRD_X_RATIO = 0.15


def best_score_path() -> Path:
    return config_path().with_name("best-score.json")


def load_best_score() -> int:
    try:
        score = json.loads(best_score_path().read_text(encoding="utf-8"))["best"]
        return max(0, int(score))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return 0


def save_best_score(score: int) -> None:
    path = best_score_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"best": max(0, score)}) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    except OSError:
        # A read-only home directory should never be able to stop the game.
        pass


def config_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "flappycodex" / "config.json"


def split_flappy_flag(arguments: Sequence[str]) -> tuple[bool, list[str]]:
    """Remove our root flag without interpreting any other Codex arguments."""
    enabled = False
    forwarded: list[str] = []
    options_finished = False
    for argument in arguments:
        if options_finished:
            forwarded.append(argument)
        elif argument == "--":
            options_finished = True
            forwarded.append(argument)
        elif argument == "--flappy":
            enabled = True
        else:
            forwarded.append(argument)
    return enabled, forwarded


def original_codex_on_path() -> str | None:
    """Find the first non-Flappy `codex` after this shim on PATH."""
    this_script = Path(__file__).resolve()
    marker = b"FLAPPY_CODEX_SHIM = 1"
    for raw_directory in os.environ.get("PATH", "").split(os.pathsep):
        directory = Path(raw_directory or os.curdir)
        candidate = directory / "codex"
        try:
            if not candidate.is_file() or not os.access(candidate, os.X_OK):
                continue
            if candidate.resolve() == this_script:
                continue
            with candidate.open("rb") as executable:
                if marker in executable.read(4096):
                    continue
        except OSError:
            continue
        return str(candidate.absolute())
    return None


def load_original_codex() -> str:
    override = os.environ.get("FLAPPY_CODEX_ORIGINAL")
    if override:
        return override
    current = original_codex_on_path()
    if current:
        return current
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        original = data["original_codex"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "Flappy Codex is not configured; run ./install.sh"
        ) from error
    if not isinstance(original, str) or not os.access(original, os.X_OK):
        raise RuntimeError(f"the saved Codex executable is unavailable: {original!r}")
    return original


def write_config(original: str, destination: str) -> int:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"original_codex": str(Path(original).resolve())}, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


@dataclass(frozen=True)
class HookSpec:
    event: str
    state: str
    matcher: str | None = None
    focus_codex: bool = False

    @property
    def event_key(self) -> str:
        labels = {
            "PermissionRequest": "permission_request",
            "PostToolUse": "post_tool_use",
            "PreToolUse": "pre_tool_use",
            "SessionEnd": "session_end",
            "SessionStart": "session_start",
            "Stop": "stop",
            "UserPromptSubmit": "user_prompt_submit",
        }
        return labels[self.event]


HOOKS = (
    HookSpec("SessionStart", "idle"),
    HookSpec("UserPromptSubmit", "working"),
    HookSpec("PreToolUse", "waiting", "request_user_input", True),
    HookSpec("PermissionRequest", "waiting", None, True),
    # PostToolUse occurs immediately after request_user_input is answered. For
    # approvals it is the first safe public hook after the approved tool ends.
    HookSpec("PostToolUse", "working", "*"),
    HookSpec("Stop", "idle"),
    HookSpec("SessionEnd", "exit"),
)


def toml_string(value: str) -> str:
    # JSON basic strings are valid TOML basic strings for the characters used
    # in paths and shell commands here.
    return json.dumps(value, ensure_ascii=False)


def hook_command(script: str, socket_path: str, top_pane: str, spec: HookSpec) -> str:
    arguments = [
        sys.executable,
        script,
        "--internal-hook",
        socket_path,
        spec.state,
        top_pane,
        "1" if spec.focus_codex else "0",
    ]
    return shlex.join(arguments)


def hook_hash(spec: HookSpec, command: str) -> str:
    """Match Codex's canonical SHA-256 identity for a command hook."""
    identity: dict[str, object] = {
        "event_name": spec.event_key,
        "hooks": [
            {
                "async": False,
                "command": command,
                "timeout": 1,
                "type": "command",
            }
        ],
    }
    if spec.matcher is not None:
        identity["matcher"] = spec.matcher
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def build_hook_overrides(script: str, socket_path: str, top_pane: str) -> list[str]:
    # Hooks may be disabled in the user's persistent config. Enable them only
    # in this process; no config file is edited.
    overrides: list[str] = ["-c", "features.hooks=true"]
    states: list[str] = []
    event_indices: dict[str, int] = {}
    for spec in HOOKS:
        command = hook_command(script, socket_path, top_pane, spec)
        handler = (
            '{ type = "command", command = ' f"{toml_string(command)}, timeout = 1 }}"
        )
        matcher = (
            "" if spec.matcher is None else f"matcher = {toml_string(spec.matcher)}, "
        )
        group = f"{{ {matcher}hooks = [{handler}] }}"
        overrides.extend(["-c", f"hooks.{spec.event}=[{group}]"])

        index = event_indices.get(spec.event, 0)
        event_indices[spec.event] = index + 1
        key = f"{SESSION_HOOK_SOURCE}:{spec.event_key}:{index}:0"
        states.append(
            f"{toml_string(key)} = {{ trusted_hash = "
            f"{toml_string(hook_hash(spec, command))} }}"
        )
    overrides.extend(["-c", "hooks.state={ " + ", ".join(states) + " }"])
    return overrides


def send_state(
    socket_path: str, state: str, top_pane: str = "", focus: bool = False
) -> None:
    """Best-effort hook transport; hook failure must never affect Codex."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.sendto(state.encode(), socket_path)
    except OSError:
        pass
    if focus and top_pane and shutil.which("tmux"):
        subprocess.run(
            ["tmux", "select-pane", "-t", top_pane],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def run_hook(socket_path: str, state: str, top_pane: str, focus: str) -> int:
    # Hook stdin is a private JSON pipe from Codex, not terminal input. We do
    # not need its payload, so exit quickly without reading any input at all.
    send_state(socket_path, state, top_pane, focus == "1")
    return 0


class FlappyGame:
    """Pure game state; terminal and Codex input remain outside this class."""

    def __init__(self, now: float | None = None, best_score: int = 0) -> None:
        self.phase = "idle"
        self.codex_state = "idle"
        self.resume_phase = "ready"
        self.width = REFERENCE_WIDTH
        self.height = REFERENCE_SKY_HEIGHT
        self.bird_y = self.height / 2
        self.velocity = 0.0
        self.score = 0
        self.best_score = max(0, best_score)
        self.pipes: list[dict[str, float | bool]] = []
        self.deadline = now if now is not None else time.monotonic()
        self.quit = False
        self.random = random.Random(0xC0DE)

    @property
    def bird_x(self) -> int:
        return max(2, min(self.width - 4, round(self.width * BIRD_X_RATIO)))

    def resize(self, width: int, height: int) -> None:
        width = max(20, width)
        height = max(5, height)
        if height != self.height:
            ratio = self.bird_y / max(1, self.height - 1)
            self.bird_y = min(height - 1.0, max(0.0, ratio * (height - 1)))
        self.width = width
        self.height = height
        half_gap = self.gap_half_distance()
        for pipe in self.pipes:
            pipe["gap"] = min(
                float(height - half_gap - 2),
                max(float(half_gap + 1), float(pipe["gap"])),
            )

    def reset(self) -> None:
        self.bird_y = max(1.0, self.height / 2)
        self.velocity = 0.0
        self.score = 0
        self.pipes = []
        self.resume_phase = "ready"

    def start_run(self) -> None:
        if not self.pipes:
            self.add_pipe(self.width + 2)
        self.phase = "working"
        self.resume_phase = "working"
        self.velocity = FLAP_VELOCITY

    def restart(self) -> None:
        if self.codex_state != "working":
            return
        self.reset()
        self.phase = "ready"

    def set_state(self, state: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if state == "exit":
            self.quit = True
            return
        self.codex_state = state
        if state == "working":
            if self.phase in {"waiting", "interrupted"}:
                if self.resume_phase in {"working", "go", "countdown"}:
                    self.phase = "countdown"
                    self.deadline = now + COUNTDOWN_SECONDS
                else:
                    self.phase = self.resume_phase
            elif self.phase == "idle":
                self.reset()
                self.phase = "ready"
        elif state in {"waiting", "interrupted"}:
            if self.phase not in {"waiting", "interrupted"}:
                self.resume_phase = self.phase
            self.phase = state
        elif state == "idle":
            self.resume_phase = self.phase
            self.phase = "idle"

    def flap(self) -> None:
        if self.codex_state != "working":
            return
        if self.phase == "ready":
            self.start_run()
        elif self.phase == "gameover":
            self.reset()
            self.start_run()
        elif self.phase in {"working", "go"}:
            self.velocity = FLAP_VELOCITY

    def add_pipe(self, x: float) -> None:
        half_gap = self.gap_half_distance()
        low = half_gap + 2
        high = self.height - half_gap - 3
        if high < low:
            center = self.height // 2
        else:
            center = self.random.randint(low, high)
            if self.pipes:
                previous = round(float(self.pipes[-1]["gap"]))
                center = min(previous + 6, max(previous - 6, center))
        self.pipes.append({"x": x, "gap": float(center), "scored": False})

    @property
    def difficulty_level(self) -> int:
        return 1 + self.score // SCORES_PER_LEVEL

    @property
    def gate_speed(self) -> float:
        increase = (self.difficulty_level - 1) * 1.2
        return min(MAX_GATE_SPEED, BASE_GATE_SPEED + increase)

    @property
    def speed_ratio(self) -> float:
        return self.gate_speed / BASE_GATE_SPEED

    def tick(self, elapsed: float, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        elapsed = min(max(elapsed, 0.0), 0.2)
        if self.phase == "countdown" and now >= self.deadline:
            self.phase = "go"
            self.deadline = now + 0.45
        elif self.phase == "go" and now >= self.deadline:
            self.phase = "working"

        if self.phase not in {"working", "go"}:
            return

        speed = self.gate_speed
        self.velocity = min(MAX_FALL_SPEED, self.velocity + GRAVITY * elapsed)
        self.bird_y += self.velocity * elapsed
        for pipe in self.pipes:
            pipe["x"] = float(pipe["x"]) - speed * elapsed
            if not bool(pipe["scored"]) and float(pipe["x"]) + 1 < self.bird_x:
                pipe["scored"] = True
                self.score += 1
                self.best_score = max(self.best_score, self.score)

        self.pipes = [pipe for pipe in self.pipes if float(pipe["x"]) > -3]
        if not self.pipes:
            self.add_pipe(self.width + 2)
        while float(self.pipes[-1]["x"]) <= self.width + 2 - self.pipe_spacing():
            self.add_pipe(float(self.pipes[-1]["x"]) + self.pipe_spacing())

        hit_boundary = self.bird_y < 0 or self.bird_y >= self.height
        hit_pipe = any(self.collides(pipe) for pipe in self.pipes)
        if hit_boundary or hit_pipe:
            self.bird_y = min(float(self.height - 1), max(0.0, self.bird_y))
            self.best_score = max(self.best_score, self.score)
            self.phase = "gameover"
            self.resume_phase = "gameover"

    def pipe_spacing(self) -> float:
        return min(GATE_SPACING, max(22.0, self.width * 0.30))

    def gap_half_distance(self) -> int:
        base = 3 if self.height >= 9 else 2
        if self.difficulty_level >= 5 and self.height >= 15:
            return 2
        return base

    def gap_bounds(self, pipe: dict[str, float | bool]) -> tuple[int, int]:
        center = round(float(pipe["gap"]))
        half_gap = self.gap_half_distance()
        return center - half_gap, center + half_gap

    def collides(self, pipe: dict[str, float | bool]) -> bool:
        pipe_x = math.floor(float(pipe["x"]))
        bird_left = self.bird_x
        bird_right = bird_left + 2
        bird_row = round(self.bird_y)
        top_cap, bottom_cap = self.gap_bounds(pipe)
        if top_cap < bird_row < bottom_cap:
            return False
        pipe_left = pipe_x - 1 if bird_row in {top_cap, bottom_cap} else pipe_x
        pipe_right = pipe_x + 1 if bird_row in {top_cap, bottom_cap} else pipe_x
        return pipe_left <= bird_right and pipe_right >= bird_left

    def bird_sprite(self) -> str:
        if self.phase == "gameover":
            return "x>_"
        if self.velocity < -1.8:
            return "/>_"
        if self.velocity > 1.8:
            return "\\>_"
        return "=>_"

    def ground(self) -> str:
        pattern = ">_.."
        repeated = pattern * (self.width // len(pattern) + 2)
        return repeated[: self.width]

    def status(self, now: float | None = None) -> str:
        now = time.monotonic() if now is None else now
        if self.phase == "countdown":
            remaining = max(1, math.ceil(self.deadline - now))
            return f"RESUMING IN {remaining}"
        return {
            "gameover": "RUN TERMINATED — PRESS SPACE TO REBOOT",
            "go": "EXECUTE!",
            "idle": "CODEX IDLE — GAME PAUSED",
            "interrupted": "CODEX INTERRUPTED — GAME PAUSED",
            "ready": "AGENT STANDBY — PRESS SPACE TO LAUNCH",
            "waiting": "CODEX NEEDS YOU — GAME PAUSED",
            "working": "SPACE / UP / CLICK — KEEP THE AGENT AIRBORNE",
        }.get(self.phase, self.phase.upper())

    def animating(self) -> bool:
        return self.phase in {"working", "countdown", "go"}


@dataclass(frozen=True)
class Cell:
    character: str = " "
    role: str = "background"


class CursesGame:
    # A midnight synth palette: amber agent, violet prompts, cyan gates,
    # magenta sparks, slate traces, and coral failures.
    COLOR_256 = {
        "background": -1,
        "white": 15,
        "agent": 221,
        "accent": 176,
        "gate": 38,
        "gate_bright": 87,
        "spark": 177,
        "trace": 60,
        "danger": 204,
    }
    COLOR_BASIC = {
        "background": -1,
        "white": curses.COLOR_WHITE,
        "agent": curses.COLOR_YELLOW,
        "accent": curses.COLOR_MAGENTA,
        "gate": curses.COLOR_CYAN,
        "gate_bright": curses.COLOR_CYAN,
        "spark": curses.COLOR_MAGENTA,
        "trace": curses.COLOR_BLUE,
        "danger": curses.COLOR_RED,
    }

    def __init__(
        self, screen: curses.window, state_socket: socket.socket, top_file: Path
    ) -> None:
        self.screen = screen
        self.state_socket = state_socket
        self.top_file = top_file
        self.game = FlappyGame(best_score=load_best_score())
        self.saved_best = self.game.best_score
        self.styles = {role: 0 for role in self.COLOR_256}
        self.last_tick = time.monotonic()
        self.last_pane_check = 0.0

    def run(self) -> None:
        self.configure_terminal()
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.screen.nodelay(True)
        self.screen.keypad(True)
        while not self.game.quit:
            self.read_states()
            self.read_keys()
            now = time.monotonic()
            self.game.tick(now - self.last_tick, now)
            self.last_tick = now
            if self.game.best_score > self.saved_best:
                save_best_score(self.game.best_score)
                self.saved_best = self.game.best_score
            self.draw(now)
            if now - self.last_pane_check >= 1.0:
                self.last_pane_check = now
                if not self.codex_pane_alive():
                    break
            if self.game.animating():
                frame_seconds = 1 / self.game.gate_speed
            else:
                frame_seconds = IDLE_SECONDS
            frame_work = time.monotonic() - now
            time.sleep(max(0.0, frame_seconds - frame_work))

    def configure_terminal(self) -> None:
        try:
            if not curses.has_colors():
                return
            curses.start_color()
            curses.use_default_colors()
            palette = self.COLOR_256 if curses.COLORS >= 256 else self.COLOR_BASIC
            for pair, (role, color) in enumerate(palette.items(), start=1):
                curses.init_pair(pair, color, -1)
                self.styles[role] = curses.color_pair(pair)
            self.styles["white"] |= curses.A_BOLD
            self.styles["agent"] |= curses.A_BOLD
            self.styles["gate_bright"] |= curses.A_BOLD
            self.styles["danger"] |= curses.A_BOLD
        except curses.error:
            self.styles = {role: 0 for role in self.COLOR_256}
        try:
            curses.mousemask(curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED)
        except curses.error:
            pass

    def read_states(self) -> None:
        while True:
            try:
                state = self.state_socket.recv(128).decode(errors="replace").strip()
            except BlockingIOError:
                return
            except OSError:
                self.game.quit = True
                return
            self.game.set_state(state)

    def read_keys(self) -> None:
        while True:
            try:
                key = self.screen.getch()
            except curses.error:
                return
            if key == -1:
                return
            if key == ord(" "):
                self.game.flap()
            elif key == curses.KEY_UP:
                self.game.flap()
            elif key == curses.KEY_MOUSE:
                try:
                    _, _, _, _, buttons = curses.getmouse()
                except curses.error:
                    continue
                if buttons & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED):
                    self.game.flap()
            elif key in (27, ord("q"), ord("Q")):
                self.game.quit = True
            elif key in (ord("r"), ord("R")) and self.game.codex_state == "working":
                self.game.restart()

    def codex_pane_alive(self) -> bool:
        try:
            details = self.top_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return True
        pane = details[0].strip() if details else ""
        if not pane:
            return True
        if len(details) > 1:
            try:
                os.kill(int(details[1]), 0)
            except (ProcessLookupError, ValueError):
                return False
            except PermissionError:
                pass
        result = subprocess.run(
            [
                "tmux",
                "display-message",
                "-p",
                "-t",
                pane,
                "#{pane_id}:#{pane_dead}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip() == f"{pane}:0"

    def safe_add(self, row: int, column: int, text: str, attributes: int = 0) -> None:
        height, width = self.screen.getmaxyx()
        if row < 0 or row >= height or column >= width:
            return
        clipped = text[: max(0, width - max(0, column) - 1)]
        try:
            self.screen.addstr(row, max(0, column), clipped, attributes)
        except curses.error:
            pass

    def centered(self, row: int, text: str, attributes: int = 0) -> None:
        _, width = self.screen.getmaxyx()
        self.safe_add(row, max(0, (width - len(text)) // 2), text, attributes)

    def put_cell(
        self, canvas: list[list[Cell]], row: int, column: int, cell: Cell
    ) -> None:
        if 0 <= row < len(canvas) and 0 <= column < len(canvas[row]):
            canvas[row][column] = cell

    def star_at(self, row: int, column: int) -> Cell:
        # A stable integer hash keeps the sky still between frames and avoids
        # consuming the gameplay RNG used for pipe openings.
        sample = (
            (column + 17) * 0x9E3779B1
            ^ (row + 29) * 0x85EBCA77
            ^ self.game.width * 0xC2B2AE3D
            ^ self.game.height * 0x27D4EB2F
        ) & 0xFFFFFFFF
        sample ^= sample >> 16
        sample = (sample * 0x7FEB352D) & 0xFFFFFFFF
        sample ^= sample >> 15
        chance = sample % 1000
        if chance < 8:
            return Cell(".", "trace")
        if chance < 13:
            return Cell("+", "spark")
        if chance < 16:
            return Cell(":", "trace")
        return Cell()

    def build_sky(self) -> list[list[Cell]]:
        canvas = [
            [self.star_at(row, column) for column in range(self.game.width)]
            for row in range(self.game.height)
        ]

        if self.game.phase in {"working", "go"}:
            bird_row = round(self.game.bird_y)
            for distance, character, role in (
                (6, ".", "trace"),
                (4, ":", "accent"),
                (2, "+", "spark"),
            ):
                self.put_cell(
                    canvas,
                    bird_row,
                    self.game.bird_x - distance,
                    Cell(character, role),
                )

        for pipe in self.game.pipes:
            pipe_x = math.floor(float(pipe["x"]))
            top_cap, bottom_cap = self.game.gap_bounds(pipe)
            for row in range(0, top_cap):
                self.put_cell(canvas, row, pipe_x, Cell("|", "gate"))
            for row in range(bottom_cap + 1, self.game.height):
                self.put_cell(canvas, row, pipe_x, Cell("|", "gate"))
            for row in (top_cap, bottom_cap):
                for offset, character in enumerate("[=]", start=-1):
                    self.put_cell(
                        canvas,
                        row,
                        pipe_x + offset,
                        Cell(character, "gate_bright"),
                    )

        bird_row = round(self.game.bird_y)
        bird_role = "danger" if self.game.phase == "gameover" else "agent"
        for offset, character in enumerate(self.game.bird_sprite()):
            self.put_cell(
                canvas,
                bird_row,
                self.game.bird_x + offset,
                Cell(character, bird_role),
            )
        return canvas

    def draw_cells(self, row: int, column: int, cells: list[Cell]) -> None:
        if not cells:
            return
        start = 0
        while start < len(cells):
            role = cells[start].role
            end = start + 1
            while end < len(cells) and cells[end].role == role:
                end += 1
            text = "".join(cell.character for cell in cells[start:end])
            self.safe_add(row, column + start, text, self.styles[role])
            start = end

    def draw_header(self, terminal_width: int) -> None:
        score = f"SCORE {self.game.score:03d}"
        best = f"BEST {self.game.best_score:03d}"
        title = (
            f"FLAPPY-CODEX // L{self.game.difficulty_level:02d}"
            f" // {self.game.speed_ratio:.2f}x"
        )
        if len(score) + len(best) + len(title) + 8 > terminal_width:
            title = f"FC // L{self.game.difficulty_level:02d}"
        self.safe_add(0, 1, score, self.styles["white"])
        self.safe_add(
            0,
            max(1, (terminal_width - len(title)) // 2),
            title,
            self.styles["accent"],
        )
        self.safe_add(
            0,
            max(1, terminal_width - len(best) - 2),
            best,
            self.styles["white"],
        )

    def draw_unboxed_overlay(
        self,
        sky_top: int,
        title: str,
        subtitle: str,
        title_role: str = "agent",
    ) -> None:
        middle = sky_top + self.game.height // 2
        self.centered(middle - 1, title, self.styles[title_role])
        self.centered(middle + 1, subtitle, self.styles["accent"])

    def draw_gameover(self, sky_top: int, field_left: int) -> None:
        if self.game.height < 9:
            self.draw_unboxed_overlay(
                sky_top, "RUN TERMINATED", "SPACE to reboot", "danger"
            )
            return
        box_width = min(36, self.game.width - 2)
        box_height = 9
        left = field_left + max(0, (self.game.width - box_width) // 2)
        top = sky_top + max(0, (self.game.height - box_height) // 2)
        inner_width = box_width - 2
        self.safe_add(
            top,
            left,
            "+" + "-" * inner_width + "+",
            self.styles["gate_bright"],
        )
        for offset in range(1, box_height - 1):
            self.safe_add(
                top + offset,
                left,
                "|" + " " * inner_width + "|",
                self.styles["gate_bright"],
            )
        self.safe_add(
            top + box_height - 1,
            left,
            "+" + "-" * inner_width + "+",
            self.styles["gate_bright"],
        )

        def box_center(row: int, text: str, role: str) -> None:
            column = left + max(1, (box_width - len(text)) // 2)
            self.safe_add(top + row, column, text, self.styles[role])

        box_center(1, "RUN TERMINATED", "danger")
        box_center(
            3,
            f"score {self.game.score} // best {self.game.best_score}",
            "white",
        )
        level = f"level {self.game.difficulty_level:02d}"
        load = f"load {self.game.speed_ratio:.2f}x"
        box_center(4, f"{level} // {load}", "white")
        box_center(6, "SPACE to reboot", "accent")

    def draw_overlay(self, now: float, sky_top: int, field_left: int) -> None:
        if self.game.phase == "ready":
            self.draw_unboxed_overlay(sky_top, "AGENT STANDBY", "SPACE to launch")
        elif self.game.phase == "idle":
            self.draw_unboxed_overlay(
                sky_top, "CODEX IDLE", "game starts while Codex works"
            )
        elif self.game.phase == "waiting":
            self.draw_unboxed_overlay(
                sky_top, "CODEX NEEDS YOU", "execution paused", "gate_bright"
            )
        elif self.game.phase == "interrupted":
            self.draw_unboxed_overlay(
                sky_top, "CODEX INTERRUPTED", "game paused", "danger"
            )
        elif self.game.phase == "countdown":
            remaining = max(1, math.ceil(self.game.deadline - now))
            self.draw_unboxed_overlay(
                sky_top, "SYNCING CONTEXT", f"resuming in {remaining}"
            )
        elif self.game.phase == "go":
            self.centered(
                sky_top + self.game.height // 2,
                "EXECUTE!",
                self.styles["agent"],
            )
        elif self.game.phase == "gameover":
            self.draw_gameover(sky_top, field_left)

    def draw(self, now: float) -> None:
        terminal_height, terminal_width = self.screen.getmaxyx()
        self.screen.erase()
        if terminal_height < 10 or terminal_width < 36:
            self.centered(
                max(1, terminal_height // 2), self.game.status(now), curses.A_BOLD
            )
            self.screen.refresh()
            return

        field_width = min(REFERENCE_WIDTH, terminal_width - 2)
        sky_height = min(REFERENCE_SKY_HEIGHT, terminal_height - 5)
        self.game.resize(field_width, sky_height)
        field_left = max(0, (terminal_width - field_width) // 2)
        sky_top = 2
        ground_row = sky_top + sky_height
        lower_border = ground_row + 1
        footer_row = lower_border + 1

        self.draw_header(terminal_width)
        self.safe_add(
            1,
            0,
            "─" * (terminal_width - 1),
            self.styles["gate_bright"],
        )
        for row, cells in enumerate(self.build_sky(), start=sky_top):
            self.draw_cells(row, field_left, cells)
        self.safe_add(
            ground_row,
            field_left,
            self.game.ground(),
            self.styles["accent"],
        )
        self.safe_add(
            lower_border,
            0,
            "─" * (terminal_width - 1),
            self.styles["gate_bright"],
        )
        self.centered(
            footer_row,
            "SPACE / ↑ / CLICK  //  KEEP THE AGENT AIRBORNE",
            self.styles["white"],
        )
        self.draw_overlay(now, sky_top, field_left)
        self.screen.refresh()


def run_game(socket_path: str, ready_file: str, top_file: str) -> int:
    path = Path(socket_path)
    ready = Path(ready_file)
    try:
        path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as state_socket:
            state_socket.bind(str(path))
            state_socket.setblocking(False)
            ready.touch()
            curses.wrapper(
                lambda screen: CursesGame(screen, state_socket, Path(top_file)).run()
            )
    finally:
        ready.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
    return 0


def tmux_output(*arguments: str) -> str:
    result = subprocess.run(
        ["tmux", *arguments],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def tmux_quiet(*arguments: str) -> None:
    subprocess.run(
        ["tmux", *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def require_pane_id(value: str) -> str:
    if len(value) < 2 or value[0] != "%" or not value[1:].isdigit():
        raise RuntimeError(f"tmux returned an invalid pane id: {value!r}")
    return value


def wait_until_ready(ready_file: Path, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready_file.exists():
            return
        time.sleep(0.03)
    raise RuntimeError("the game pane did not start")


def codex_arguments(
    original: str,
    forwarded: Sequence[str],
    script: str,
    socket_path: str,
    top_pane: str,
) -> list[str]:
    return [
        original,
        *build_hook_overrides(script, socket_path, top_pane),
        *forwarded,
    ]


def supervise_codex(command: Sequence[str], socket_path: str) -> int:
    """Run Codex without mediating its terminal, input, or output."""
    process = subprocess.Popen(command)
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            # Codex receives Ctrl-C directly because it shares this pane and
            # foreground process group. This only updates the companion pane.
            send_state(socket_path, "interrupted")


def run_inside_tmux(original: str, forwarded: Sequence[str], runtime: Path) -> int:
    raw_top_pane = os.environ.get("TMUX_PANE")
    if not raw_top_pane:
        raise RuntimeError("tmux did not expose the current pane")
    top_pane = require_pane_id(raw_top_pane)
    script = str(Path(__file__).resolve())
    socket_path = runtime / "state.sock"
    ready_file = runtime / "ready"
    top_file = runtime / "codex-pane"
    top_file.write_text(f"{top_pane}\n{os.getpid()}\n", encoding="utf-8")
    game_command = shlex.join(
        [script, "--internal-game", str(socket_path), str(ready_file), str(top_file)]
    )
    game_pane: str | None = None
    try:
        game_pane = require_pane_id(
            tmux_output(
                "split-window",
                "-v",
                "-l",
                "42%",
                "-t",
                top_pane,
                "-P",
                "-F",
                "#{pane_id}",
                "--",
                game_command,
            )
        )
        tmux_quiet("select-pane", "-t", game_pane, "-P", "bg=#07111f")
        wait_until_ready(ready_file)
        tmux_quiet("select-pane", "-t", top_pane)
        return supervise_codex(
            codex_arguments(original, forwarded, script, str(socket_path), top_pane),
            str(socket_path),
        )
    finally:
        if game_pane is not None:
            tmux_quiet("kill-pane", "-t", game_pane)


def run_private_tmux(original: str, forwarded: Sequence[str], runtime: Path) -> int:
    script = str(Path(__file__).resolve())
    socket_path = runtime / "state.sock"
    ready_file = runtime / "ready"
    top_file = runtime / "codex-pane"
    session = f"flappy-codex-{os.getpid()}"
    game_command = shlex.join(
        [script, "--internal-game", str(socket_path), str(ready_file), str(top_file)]
    )
    try:
        game_pane = require_pane_id(
            tmux_output(
                "new-session",
                "-d",
                "-s",
                session,
                "-c",
                str(Path.cwd()),
                "-P",
                "-F",
                "#{pane_id}",
                game_command,
            )
        )
        wait_until_ready(ready_file)
        top_pane = require_pane_id(
            tmux_output(
                "split-window",
                "-b",
                "-v",
                "-l",
                "58%",
                "-t",
                game_pane,
                "-c",
                str(Path.cwd()),
                "-P",
                "-F",
                "#{pane_id}",
                "--",
                "sleep 86400",
            )
        )
        tmux_quiet("select-pane", "-t", game_pane, "-P", "bg=#07111f")
        top_file.write_text(f"{top_pane}\n{os.getpid()}\n", encoding="utf-8")
        command = shlex.join(
            [
                script,
                "--internal-codex",
                str(socket_path),
                *codex_arguments(
                    original, forwarded, script, str(socket_path), top_pane
                ),
            ]
        )
        tmux_output("respawn-pane", "-k", "-t", top_pane, "--", command)
        tmux_quiet("set-option", "-t", session, "status", "off")
        tmux_quiet("set-option", "-t", session, "mouse", "on")
        tmux_quiet(
            "set-hook",
            "-t",
            session,
            "client-detached",
            f"kill-session -t {session}",
        )
        tmux_quiet("select-pane", "-t", top_pane)
        return subprocess.call(["tmux", "attach-session", "-t", session])
    finally:
        tmux_quiet("kill-session", "-t", session)


def launch_flappy(original: str, forwarded: Sequence[str]) -> int:
    if os.name != "posix" or not hasattr(socket, "AF_UNIX"):
        raise RuntimeError("Flappy Codex currently supports macOS and Linux terminals")
    if not shutil.which("tmux"):
        raise RuntimeError("tmux is required for the isolated game pane")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("--flappy requires an interactive terminal")
    runtime = Path(tempfile.mkdtemp(prefix="flappycodex-"))
    try:
        if os.environ.get("TMUX"):
            return run_inside_tmux(original, forwarded, runtime)
        return run_private_tmux(original, forwarded, runtime)
    finally:
        shutil.rmtree(runtime, ignore_errors=True)


def main(arguments: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if arguments[:1] == ["--internal-configure"]:
        return write_config(arguments[1], arguments[2])
    if arguments[:1] == ["--internal-hook"]:
        return run_hook(arguments[1], arguments[2], arguments[3], arguments[4])
    if arguments[:1] == ["--internal-game"]:
        return run_game(arguments[1], arguments[2], arguments[3])
    if arguments[:1] == ["--internal-codex"]:
        return supervise_codex(arguments[2:], arguments[1])

    original = load_original_codex()
    enabled, forwarded = split_flappy_flag(arguments)
    if not enabled:
        os.execv(original, [original, *forwarded])
        raise AssertionError("execv returned unexpectedly")
    try:
        return launch_flappy(original, forwarded)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Flappy Codex: {error}", file=sys.stderr)
        print(
            "Codex was not started. Run plain `codex` or fix the issue above.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
