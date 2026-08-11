import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "flappycodex.py"
SPEC = importlib.util.spec_from_file_location("flappycodex", MODULE_PATH)
assert SPEC and SPEC.loader
flappy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = flappy
SPEC.loader.exec_module(flappy)


class ArgumentTests(unittest.TestCase):
    def test_plain_arguments_are_untouched(self):
        enabled, forwarded = flappy.split_flappy_flag(
            ["--model", "gpt-5", "prompt", "with", "spaces"]
        )
        self.assertFalse(enabled)
        self.assertEqual(forwarded, ["--model", "gpt-5", "prompt", "with", "spaces"])

    def test_flappy_is_removed_and_normal_arguments_remain_ordered(self):
        enabled, forwarded = flappy.split_flappy_flag(
            ["--flappy", "--model", "gpt-5", "resume", "--last"]
        )
        self.assertTrue(enabled)
        self.assertEqual(forwarded, ["--model", "gpt-5", "resume", "--last"])

    def test_flappy_after_separator_belongs_to_codex(self):
        enabled, forwarded = flappy.split_flappy_flag(["--", "--flappy"])
        self.assertFalse(enabled)
        self.assertEqual(forwarded, ["--", "--flappy"])

    def test_plain_launch_execs_original_with_stdio_and_arguments_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original-codex"
            original.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "print(json.dumps(sys.argv[1:]))\n"
                "print('STDIN=' + sys.stdin.read())\n",
                encoding="utf-8",
            )
            original.chmod(0o755)
            environment = dict(**flappy.os.environ)
            environment["FLAPPY_CODEX_ORIGINAL"] = str(original)
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), "--model", "gpt-5", "hello world"],
                input="prompt input",
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                check=False,
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout.splitlines(),
            ['["--model", "gpt-5", "hello world"]', "STDIN=prompt input"],
        )
        self.assertEqual(result.stderr, "")

    def test_old_internal_flag_name_is_forwarded_to_codex(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original-codex"
            original.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "print(json.dumps(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            original.chmod(0o755)
            environment = dict(**flappy.os.environ)
            environment["FLAPPY_CODEX_ORIGINAL"] = str(original)
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), "--internal-hook", "example"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                check=False,
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), '["--internal-hook", "example"]')
        self.assertEqual(result.stderr, "")

    def test_malformed_namespaced_internal_calls_fail_without_tracebacks(self):
        flags = (
            flappy.INTERNAL_CONFIGURE,
            flappy.INTERNAL_HOOK,
            flappy.INTERNAL_GAME,
            flappy.INTERNAL_CODEX,
        )
        for flag in flags:
            with self.subTest(flag=flag):
                error = io.StringIO()
                with contextlib.redirect_stderr(error):
                    result = flappy.main([flag])
                self.assertEqual(result, 2)
                self.assertIn("expects", error.getvalue())
                self.assertNotIn("Traceback", error.getvalue())

    def test_original_codex_tracks_the_current_path(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "codex"
            original.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            original.chmod(0o755)
            with mock.patch.dict(
                flappy.os.environ,
                {"PATH": directory},
                clear=True,
            ):
                discovered = flappy.load_original_codex()
        self.assertEqual(discovered, str(original))


class HookTests(unittest.TestCase):
    def test_hook_hash_matches_the_pinned_codex_identity_format(self):
        self.assertEqual(
            flappy.hook_hash(flappy.HOOKS[0], "/usr/bin/python3 hook.py"),
            "sha256:18e5ffdb184e4e900526c13f4d54967533c39e13fdd78ed29b01203e58ddf569",
        )

    def test_overrides_are_session_only_trusted_hooks(self):
        arguments = flappy.build_hook_overrides(
            "/opt/flappy codex.py", "/tmp/state.sock", "%7"
        )
        joined = "\n".join(arguments)
        self.assertIn("hooks.UserPromptSubmit", joined)
        self.assertIn("hooks.PermissionRequest", joined)
        self.assertIn("hooks.state", joined)
        self.assertIn("features.hooks=true", joined)
        self.assertIn("trusted_hash", joined)
        self.assertNotIn("bypass", joined)
        self.assertIn(flappy.INTERNAL_HOOK, joined)

    def test_hook_transport_sends_state_without_reading_codex_input(self):
        client = mock.MagicMock()
        connected_client = client.__enter__.return_value
        with mock.patch.object(flappy.socket, "socket", return_value=client):
            flappy.send_state("/tmp/state.sock", "waiting")
        connected_client.sendto.assert_called_once_with(b"waiting", "/tmp/state.sock")


class GameStateTests(unittest.TestCase):
    def test_work_waits_for_first_launch_input(self):
        game = flappy.FlappyGame(now=0)
        game.set_state("working", now=0)
        self.assertEqual(game.phase, "ready")
        self.assertEqual(game.pipes, [])

        game.flap()

        self.assertEqual(game.phase, "working")
        self.assertEqual(game.velocity, flappy.FLAP_VELOCITY)
        self.assertEqual(len(game.pipes), 1)

    def test_waiting_freezes_game(self):
        game = flappy.FlappyGame(now=0)
        game.set_state("working", now=0)
        game.bird_y = 3.0
        game.velocity = 1.0
        game.set_state("waiting", now=1)
        game.tick(1.0, now=2)
        self.assertEqual(game.bird_y, 3.0)
        self.assertEqual(game.velocity, 1.0)

    def test_answer_starts_exact_three_second_countdown(self):
        game = flappy.FlappyGame(now=0)
        game.set_state("working", now=0)
        game.flap()
        game.set_state("waiting", now=1)
        game.set_state("working", now=2)
        self.assertEqual(game.phase, "countdown")
        self.assertEqual(game.deadline, 5.0)
        game.tick(0.1, now=4.99)
        self.assertEqual(game.phase, "countdown")
        game.tick(0.1, now=5.0)
        self.assertEqual(game.phase, "go")
        game.tick(0.1, now=5.46)
        self.assertEqual(game.phase, "working")

    def test_idle_and_interrupt_pause(self):
        game = flappy.FlappyGame(now=0)
        game.set_state("working", now=0)
        game.set_state("idle", now=1)
        self.assertEqual(game.phase, "idle")
        game.set_state("working", now=2)
        game.set_state("interrupted", now=3)
        self.assertEqual(game.phase, "interrupted")

    def test_exit_cleans_up_game_loop(self):
        game = flappy.FlappyGame(now=0)
        game.set_state("exit", now=1)
        self.assertTrue(game.quit)

    def test_agent_cursor_tilts_with_vertical_velocity(self):
        game = flappy.FlappyGame(now=0)
        game.velocity = -4
        self.assertEqual(game.bird_sprite(), "/>_")
        game.velocity = 0
        self.assertEqual(game.bird_sprite(), "=>_")
        game.velocity = 4
        self.assertEqual(game.bird_sprite(), "\\>_")
        game.phase = "gameover"
        self.assertEqual(game.bird_sprite(), "x>_")

    def test_difficulty_ramps_every_two_points_and_has_a_cap(self):
        game = flappy.FlappyGame(now=0)
        self.assertEqual(game.difficulty_level, 1)
        self.assertEqual(game.gate_speed, flappy.BASE_GATE_SPEED)
        self.assertEqual(game.gap_half_distance(), 3)

        game.score = 2
        self.assertEqual(game.difficulty_level, 2)
        self.assertGreater(game.gate_speed, flappy.BASE_GATE_SPEED)

        game.score = 8
        self.assertEqual(game.gap_half_distance(), 2)

        game.score = 999
        self.assertEqual(game.gate_speed, flappy.MAX_GATE_SPEED)

    def test_one_render_interval_advances_a_gate_by_one_cell(self):
        game = flappy.FlappyGame(now=0)
        game.set_state("working", now=0)
        game.flap()
        start = float(game.pipes[0]["x"])

        game.tick(1 / game.gate_speed, now=0)

        self.assertAlmostEqual(start - float(game.pipes[0]["x"]), 1.0)

    def test_score_updates_best_after_a_pipe_passes(self):
        game = flappy.FlappyGame(now=0, best_score=2)
        game.set_state("working", now=0)
        game.flap()
        game.bird_y = game.pipes[0]["gap"]
        game.pipes[0]["x"] = game.bird_x - 2
        game.tick(0, now=0)
        self.assertEqual(game.score, 1)
        self.assertEqual(game.best_score, 2)

        game.score = 2
        game.pipes[0]["scored"] = False
        game.tick(0, now=0)
        self.assertEqual(game.score, 3)
        self.assertEqual(game.best_score, 3)

    def test_codex_gates_use_brackets_and_token_streams(self):
        frontend = flappy.CursesGame(mock.Mock(), mock.Mock(), Path("/missing"))
        game = flappy.FlappyGame(now=0)
        game.resize(100, 27)
        game.phase = "working"
        game.bird_y = 13
        game.pipes = [{"x": 50.0, "gap": 13.0, "scored": False}]
        frontend.game = game

        sky = frontend.build_sky()
        top_cap, bottom_cap = game.gap_bounds(game.pipes[0])
        self.assertEqual(
            "".join(sky[top_cap][x].character for x in range(49, 52)), "[=]"
        )
        self.assertEqual(
            "".join(sky[bottom_cap][x].character for x in range(49, 52)),
            "[=]",
        )
        self.assertEqual(sky[top_cap - 1][50].character, "|")
        self.assertEqual(sky[bottom_cap + 1][50].character, "|")
        self.assertEqual(game.ground()[:8], ">_..>_..")


class LifecycleTests(unittest.TestCase):
    def test_curses_wrapper_runs_the_game_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "state.sock")
            ready_path = str(Path(directory) / "ready")
            top_path = str(Path(directory) / "top")
            state_socket = mock.MagicMock()
            socket_context = mock.MagicMock()
            socket_context.__enter__.return_value = state_socket
            screen = mock.Mock()
            game = mock.Mock()

            with (
                mock.patch.object(flappy.socket, "socket", return_value=socket_context),
                mock.patch.object(
                    flappy, "CursesGame", return_value=game
                ) as game_class,
                mock.patch.object(
                    flappy.curses,
                    "wrapper",
                    side_effect=lambda callback: callback(screen),
                ),
            ):
                result = flappy.run_game(socket_path, ready_path, top_path)

        self.assertEqual(result, 0)
        game_class.assert_called_once_with(screen, state_socket, Path(top_path))
        game.run.assert_called_once_with()

    def test_game_detects_a_vanished_codex_pane(self):
        with tempfile.TemporaryDirectory() as directory:
            top_file = Path(directory) / "top"
            top_file.write_text(f"%7\n{flappy.os.getpid()}\n", encoding="utf-8")
            frontend = flappy.CursesGame(mock.Mock(), mock.Mock(), top_file)
            missing = subprocess.CompletedProcess(["tmux"], returncode=0, stdout="")
            with mock.patch.object(flappy.subprocess, "run", return_value=missing):
                self.assertFalse(frontend.codex_pane_alive())

    def test_game_accepts_only_the_exact_live_codex_pane(self):
        with tempfile.TemporaryDirectory() as directory:
            top_file = Path(directory) / "top"
            top_file.write_text(f"%7\n{flappy.os.getpid()}\n", encoding="utf-8")
            frontend = flappy.CursesGame(mock.Mock(), mock.Mock(), top_file)
            live = subprocess.CompletedProcess(["tmux"], returncode=0, stdout="%7:0\n")
            with mock.patch.object(flappy.subprocess, "run", return_value=live):
                self.assertTrue(frontend.codex_pane_alive())

    def test_missing_tmux_fails_before_codex_starts(self):
        with mock.patch.object(flappy.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "tmux is required"):
                flappy.launch_flappy("/bin/false", [])

    def test_runtime_directory_is_removed_when_launch_fails(self):
        with tempfile.TemporaryDirectory() as parent:
            runtime = Path(parent) / "runtime"
            with (
                mock.patch.object(flappy.shutil, "which", return_value="/usr/bin/tmux"),
                mock.patch.object(flappy.sys.stdin, "isatty", return_value=True),
                mock.patch.object(flappy.sys.stdout, "isatty", return_value=True),
                mock.patch.object(
                    flappy.tempfile, "mkdtemp", return_value=str(runtime)
                ),
                mock.patch.dict(flappy.os.environ, {}, clear=True),
                mock.patch.object(
                    flappy,
                    "run_private_tmux",
                    side_effect=RuntimeError("simulated failure"),
                ),
            ):
                runtime.mkdir()
                with self.assertRaisesRegex(RuntimeError, "simulated failure"):
                    flappy.launch_flappy("/bin/false", [])
            self.assertFalse(runtime.exists())

    def test_ctrl_c_updates_game_without_swallowing_codex_exit(self):
        process = mock.Mock()
        process.wait.side_effect = [KeyboardInterrupt, 7]
        with (
            mock.patch.object(flappy.subprocess, "Popen", return_value=process),
            mock.patch.object(flappy, "send_state") as send_state,
        ):
            result = flappy.supervise_codex(["codex"], "/tmp/state.sock")
        self.assertEqual(result, 7)
        send_state.assert_called_once_with("/tmp/state.sock", "interrupted")

    def test_private_tmux_is_isolated_and_never_binds_prompt_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / "ready").touch()
            with (
                mock.patch.object(
                    flappy,
                    "tmux_output",
                    side_effect=["%1", "%2", ""],
                ) as tmux_output,
                mock.patch.object(flappy, "tmux_quiet") as tmux_quiet,
                mock.patch.object(flappy.subprocess, "call", return_value=4),
            ):
                result = flappy.run_private_tmux(
                    "/usr/bin/codex", ["--model", "gpt-5"], runtime
                )

        self.assertEqual(result, 4)
        flattened = "\n".join(
            " ".join(call.args) for call in tmux_output.call_args_list
        )
        self.assertIn("split-window -b -v -l 58%", flattened)
        self.assertIn(flappy.INTERNAL_CODEX, flattened)
        self.assertIn("--model gpt-5", flattened)
        self.assertNotIn("bind-key", flattened)
        tmux_quiet.assert_any_call("select-pane", "-t", "%2")
        tmux_quiet.assert_any_call("select-pane", "-t", "%1", "-P", "bg=#07111f")
        self.assertTrue(
            any(
                call.args[:3] == ("set-hook", "-t", mock.ANY)
                and "client-detached" in call.args
                for call in tmux_quiet.call_args_list
            )
        )
        tmux_quiet.assert_any_call("kill-session", "-t", mock.ANY)


if __name__ == "__main__":
    unittest.main()
