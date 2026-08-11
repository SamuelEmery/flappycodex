import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


PROJECT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_DIR / "flappycodex.py"
SMOKE_ENABLED = os.environ.get("FLAPPY_CODEX_TMUX_SMOKE") == "1"


@unittest.skipUnless(
    SMOKE_ENABLED and sys.platform.startswith("linux") and shutil.which("tmux"),
    "set FLAPPY_CODEX_TMUX_SMOKE=1 on Linux with tmux installed",
)
class TmuxIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.server = f"flappy-smoke-{os.getpid()}-{self.root.name[-6:]}"
        self.session = "flappy-smoke"
        self.stage_file = self.root / "stage"
        self.continue_file = self.root / "continue"
        self.finish_file = self.root / "finish"
        self.events_file = self.root / "events"
        self.arguments_file = self.root / "arguments.json"
        self.fake_codex = self.root / "codex"
        self.fake_codex.write_text(self.fake_codex_source(), encoding="utf-8")
        self.fake_codex.chmod(0o755)
        self.environment = {
            **os.environ,
            "TERM": "xterm-256color",
            "FLAPPY_CODEX_ORIGINAL": str(self.fake_codex),
            "FLAPPY_SMOKE_STAGE": str(self.stage_file),
            "FLAPPY_SMOKE_CONTINUE": str(self.continue_file),
            "FLAPPY_SMOKE_FINISH": str(self.finish_file),
            "FLAPPY_SMOKE_EVENTS": str(self.events_file),
            "FLAPPY_SMOKE_ARGUMENTS": str(self.arguments_file),
        }

    def tearDown(self):
        subprocess.run(
            ["tmux", "-L", self.server, "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        self.temporary.cleanup()

    @staticmethod
    def fake_codex_source():
        return r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

stage = Path(os.environ["FLAPPY_SMOKE_STAGE"])
continue_file = Path(os.environ["FLAPPY_SMOKE_CONTINUE"])
finish_file = Path(os.environ["FLAPPY_SMOKE_FINISH"])
events_file = Path(os.environ["FLAPPY_SMOKE_EVENTS"])
Path(os.environ["FLAPPY_SMOKE_ARGUMENTS"]).write_text(
    json.dumps(sys.argv[1:]), encoding="utf-8"
)

commands = {}
pattern = re.compile(r'command = ("(?:\\.|[^"\\])*")')
for argument in sys.argv[1:]:
    if not argument.startswith("hooks."):
        continue
    match = pattern.search(argument)
    if match:
        event = argument[len("hooks."):].split("=", 1)[0]
        commands[event] = json.loads(match.group(1))


def fire(event):
    command = commands[event]
    result = subprocess.run(
        shlex.split(command),
        input="{}\n",
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
        timeout=5,
    )
    if result.returncode:
        raise SystemExit(f"{event} hook failed: {result.stderr}")
    with events_file.open("a", encoding="utf-8") as handle:
        handle.write(event + "\n")


def wait_for(path):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.03)
    raise SystemExit(f"timed out waiting for {path.name}")


required = {
    "SessionStart",
    "UserPromptSubmit",
    "PermissionRequest",
    "PostToolUse",
    "Stop",
    "SessionEnd",
}
missing = sorted(required - commands.keys())
if missing:
    raise SystemExit(f"missing generated hooks: {missing}")

fire("SessionStart")
fire("UserPromptSubmit")
stage.write_text("working", encoding="utf-8")
wait_for(continue_file)
fire("PermissionRequest")
stage.write_text("waiting", encoding="utf-8")
wait_for(finish_file)
fire("PostToolUse")
fire("Stop")
fire("SessionEnd")
'''

    def tmux(self, *arguments):
        return subprocess.run(
            ["tmux", "-L", self.server, *arguments],
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def wait_for(self, predicate, description, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        self.fail(f"timed out waiting for {description}\n{self.capture_all_panes()}")

    def stage_is(self, expected):
        try:
            return self.stage_file.read_text(encoding="utf-8") == expected
        except OSError:
            return False

    def capture_all_panes(self):
        panes = self.tmux(
            "list-panes", "-t", self.session, "-F", "#{pane_id}"
        )
        if panes.returncode:
            return panes.stderr
        captures = []
        for pane in panes.stdout.splitlines():
            captured = self.tmux("capture-pane", "-p", "-t", pane)
            captures.append(f"pane {pane}:\n{captured.stdout}")
        return "\n".join(captures)

    def test_hooks_drive_a_real_tmux_game_and_the_session_tears_down(self):
        command = shlex.join(
            [sys.executable, str(MODULE_PATH), "--flappy", "--model", "smoke"]
        )
        started = self.tmux(
            "new-session",
            "-d",
            "-s",
            self.session,
            "-x",
            "100",
            "-y",
            "42",
            command,
        )
        self.assertEqual(started.returncode, 0, started.stderr)

        self.wait_for(lambda: self.stage_is("working"), "working hook state")
        panes = self.tmux("list-panes", "-t", self.session, "-F", "#{pane_id}")
        self.assertEqual(panes.returncode, 0, panes.stderr)
        self.assertEqual(len(panes.stdout.splitlines()), 2)
        self.wait_for(
            lambda: "AGENT STANDBY" in self.capture_all_panes(),
            "working state to render",
        )

        self.continue_file.touch()
        self.wait_for(lambda: self.stage_is("waiting"), "waiting hook state")
        self.wait_for(
            lambda: "CODEX NEEDS YOU" in self.capture_all_panes(),
            "waiting state to render",
        )

        self.finish_file.touch()
        self.wait_for(
            lambda: self.tmux("has-session", "-t", self.session).returncode != 0,
            "tmux session teardown",
        )

        self.assertEqual(
            self.events_file.read_text(encoding="utf-8").splitlines(),
            [
                "SessionStart",
                "UserPromptSubmit",
                "PermissionRequest",
                "PostToolUse",
                "Stop",
                "SessionEnd",
            ],
        )
        arguments = json.loads(self.arguments_file.read_text(encoding="utf-8"))
        joined = "\n".join(arguments)
        self.assertIn("hooks.state", joined)
        self.assertIn("trusted_hash", joined)
        self.assertNotIn("bypass", joined)


if __name__ == "__main__":
    unittest.main()
