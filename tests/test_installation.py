import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

PROJECT_DIR = Path(__file__).resolve().parents[1]


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.data_home = self.root / "data"
        self.config_home = self.root / "config"
        self.bin_dir = self.root / "custom-bin"
        self.original_bin = self.root / "original-bin"
        self.home.mkdir()
        self.original_bin.mkdir()
        original = self.original_bin / "codex"
        original.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        original.chmod(0o755)
        self.environment = {
            **os.environ,
            "HOME": str(self.home),
            "XDG_DATA_HOME": str(self.data_home),
            "XDG_CONFIG_HOME": str(self.config_home),
            "FLAPPY_CODEX_BIN_DIR": str(self.bin_dir),
            # Keep the Python selected by setup-python (and pyenv locally).
            # macOS still ships an older /usr/bin/python3, so replacing PATH
            # would test that interpreter instead of the matrix version.
            "PATH": os.pathsep.join(
                (str(self.original_bin), os.environ.get("PATH", ""))
            ),
            "SHELL": "/bin/bash",
        }

    def tearDown(self):
        self.temporary.cleanup()

    def run_script(self, script, *arguments, environment=None):
        result = subprocess.run(
            ["bash", str(PROJECT_DIR / script), *arguments],
            cwd=PROJECT_DIR,
            env=self.environment if environment is None else environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"{script} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        return result

    def run_script_without_assertion(self, script, *arguments, environment=None):
        return subprocess.run(
            ["bash", str(PROJECT_DIR / script), *arguments],
            cwd=PROJECT_DIR,
            env=self.environment if environment is None else environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def install(self):
        bashrc = self.home / ".bashrc"
        bashrc.write_text("export PATH=/opt/tools:$PATH\n", encoding="utf-8")
        bashrc.chmod(0o640)
        self.run_script("install.sh")
        return bashrc

    def test_custom_install_is_recorded_and_fully_reversible(self):
        bashrc = self.install()
        shim = self.bin_dir / "codex"
        config_file = self.config_home / "flappycodex" / "config.json"
        score_file = config_file.with_name("best-score.json")
        installed_script = self.data_home / "flappycodex" / "flappycodex.py"
        shell_backup = bashrc.with_name(".bashrc.flappycodex.bak")

        configuration = json.loads(config_file.read_text(encoding="utf-8"))
        self.assertEqual(configuration["shim"], str(shim.resolve()))
        self.assertTrue(shim.is_file())
        self.assertTrue(installed_script.is_file())
        self.assertEqual(
            shell_backup.read_text(encoding="utf-8"), "export PATH=/opt/tools:$PATH\n"
        )
        self.assertEqual(bashrc.stat().st_mode & 0o777, 0o640)
        self.assertIn("# >>> Flappy Codex PATH >>>", bashrc.read_text(encoding="utf-8"))
        score_file.write_text('{"best": 12}\n', encoding="utf-8")

        uninstall_environment = self.environment.copy()
        uninstall_environment.pop("FLAPPY_CODEX_BIN_DIR")
        self.run_script("uninstall.sh", environment=uninstall_environment)

        self.assertFalse(shim.exists())
        self.assertFalse(config_file.exists())
        self.assertFalse(score_file.exists())
        self.assertFalse(installed_script.exists())
        self.assertFalse(shell_backup.exists())
        self.assertEqual(bashrc.stat().st_mode & 0o777, 0o640)
        self.assertEqual(
            bashrc.read_text(encoding="utf-8"),
            "export PATH=/opt/tools:$PATH\n",
        )

    def test_uninstall_can_preserve_the_best_score(self):
        self.install()
        score_file = self.config_home / "flappycodex" / "best-score.json"
        score_file.write_text('{"best": 7}\n', encoding="utf-8")

        self.run_script("uninstall.sh", "--keep-score")

        self.assertEqual(
            json.loads(score_file.read_text(encoding="utf-8")), {"best": 7}
        )

    def test_uninstall_explains_the_parent_shell_command_cache(self):
        self.install()

        result = self.run_script("uninstall.sh")

        self.assertIn("hash -r", result.stdout)
        self.assertIn("rehash", result.stdout)

    def test_reinstall_removes_a_previous_shim_location(self):
        self.install()
        old_shim = self.bin_dir / "codex"
        self.assertTrue(old_shim.exists())

        reinstall_environment = self.environment.copy()
        reinstall_environment.pop("FLAPPY_CODEX_BIN_DIR")
        self.run_script("install.sh", environment=reinstall_environment)

        new_shim = self.home / ".local" / "bin" / "codex"
        configuration = json.loads(
            (self.config_home / "flappycodex" / "config.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(old_shim.exists())
        self.assertTrue(new_shim.exists())
        self.assertEqual(configuration["shim"], str(new_shim.resolve()))

    def test_exported_codex_function_is_not_recorded_as_the_executable(self):
        result = subprocess.run(
            [
                "bash",
                "-c",
                'codex() { :; }; export -f codex; exec bash "$1"',
                "flappycodex-test",
                str(PROJECT_DIR / "install.sh"),
            ],
            cwd=PROJECT_DIR,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"install failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        configuration = json.loads(
            (self.config_home / "flappycodex" / "config.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            configuration["original_codex"],
            str((self.original_bin / "codex").resolve()),
        )

    def test_occupied_fallback_shim_is_never_overwritten(self):
        environment = self.environment.copy()
        environment.pop("FLAPPY_CODEX_BIN_DIR")
        default_shim = self.home / ".local" / "bin" / "codex"
        default_shim.parent.mkdir(parents=True)
        default_shim.write_text("foreign default executable\n", encoding="utf-8")
        fallback_shim = self.data_home / "flappycodex" / "bin" / "codex"
        fallback_shim.parent.mkdir(parents=True)
        foreign_contents = "foreign fallback executable\n"
        fallback_shim.write_text(foreign_contents, encoding="utf-8")
        fallback_shim.chmod(0o755)

        result = self.run_script_without_assertion(
            "install.sh", environment=environment
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is not a Flappy Codex launcher", result.stderr)
        self.assertEqual(fallback_shim.read_text(encoding="utf-8"), foreign_contents)
        self.assertFalse(
            (self.config_home / "flappycodex" / "config.json").exists()
        )

    def test_uninstall_removes_a_shell_config_created_by_install(self):
        bashrc = self.home / ".bashrc"
        self.assertFalse(bashrc.exists())

        self.run_script("install.sh")
        self.run_script("install.sh")
        configuration = json.loads(
            (self.config_home / "flappycodex" / "config.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn(
            str(bashrc.absolute()), configuration["created_shell_configs"]
        )

        self.run_script("uninstall.sh")

        self.assertFalse(bashrc.exists())

    def test_uninstall_preserves_a_preexisting_empty_shell_config(self):
        bashrc = self.home / ".bashrc"
        bashrc.touch()

        self.run_script("install.sh")
        self.run_script("uninstall.sh")

        self.assertTrue(bashrc.is_file())
        self.assertEqual(bashrc.read_text(encoding="utf-8"), "")

    def test_upgrade_creates_a_clean_backup_from_an_existing_path_block(self):
        bashrc = self.home / ".bashrc"
        bashrc.write_text(
            "export EDITOR=vim\n"
            "\n"
            "# >>> Flappy Codex PATH >>>\n"
            "export PATH=/old/flappy/bin:$PATH\n"
            "# <<< Flappy Codex PATH <<<\n",
            encoding="utf-8",
        )

        self.run_script("install.sh")

        backup = bashrc.with_name(".bashrc.flappycodex.bak")
        self.assertEqual(backup.read_text(encoding="utf-8"), "export EDITOR=vim\n")
        contents = bashrc.read_text(encoding="utf-8")
        self.assertNotIn("/old/flappy/bin", contents)
        self.assertIn(str(self.bin_dir), contents)

        self.run_script("uninstall.sh")

        self.assertFalse(backup.exists())
        self.assertEqual(bashrc.read_text(encoding="utf-8"), "export EDITOR=vim\n")

    def test_uninstall_retains_backup_when_shell_config_changed(self):
        bashrc = self.install()
        with bashrc.open("a", encoding="utf-8") as handle:
            handle.write("alias after-install='true'\n")

        result = self.run_script("uninstall.sh")

        backup = bashrc.with_name(".bashrc.flappycodex.bak")
        self.assertTrue(backup.exists())
        self.assertEqual(
            backup.read_text(encoding="utf-8"), "export PATH=/opt/tools:$PATH\n"
        )
        self.assertIn("Shell backup retained", result.stdout)
        contents = bashrc.read_text(encoding="utf-8")
        self.assertNotIn("Flappy Codex PATH", contents)
        self.assertIn("alias after-install='true'", contents)

    def test_install_and_uninstall_preserve_a_symlinked_shell_config(self):
        dotfiles = self.home / "dotfiles"
        dotfiles.mkdir()
        target = dotfiles / "bashrc"
        target.write_text("export EDITOR=vim\n", encoding="utf-8")
        target.chmod(0o640)
        bashrc = self.home / ".bashrc"
        bashrc.symlink_to(target)

        self.run_script("install.sh")

        backup = target.with_name("bashrc.flappycodex.bak")
        self.assertTrue(bashrc.is_symlink())
        self.assertTrue(backup.exists())
        self.assertIn("Flappy Codex PATH", target.read_text(encoding="utf-8"))
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)

        self.run_script("uninstall.sh")

        self.assertTrue(bashrc.is_symlink())
        self.assertFalse(backup.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "export EDITOR=vim\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_marker_file_not_named_codex_is_never_deleted(self):
        protected = self.root / "keep-me.py"
        protected.write_text("FLAPPY_CODEX_SHIM = 1\n", encoding="utf-8")
        config_dir = self.config_home / "flappycodex"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "original_codex": str(self.original_bin / "codex"),
                    "shim": str(protected),
                }
            ),
            encoding="utf-8",
        )

        self.install()
        self.assertTrue(protected.exists())

        config_file.write_text(json.dumps({"shim": str(protected)}), encoding="utf-8")
        self.run_script("uninstall.sh")

        self.assertTrue(protected.exists())

    def test_uninstall_removes_the_legacy_path_block_only(self):
        bashrc = self.home / ".bashrc"
        bashrc.write_text(
            "export EDITOR=vim\n"
            "# Flappy Codex PATH (/tmp/old-flappy-bin)\n"
            "export PATH=/tmp/old-flappy-bin:$PATH\n"
            "alias codex-info='codex --version'\n",
            encoding="utf-8",
        )

        self.run_script("uninstall.sh")

        self.assertFalse(bashrc.with_name(".bashrc.flappycodex.bak").exists())
        self.assertEqual(
            bashrc.read_text(encoding="utf-8"),
            "export EDITOR=vim\nalias codex-info='codex --version'\n",
        )

    def test_uninstall_preserves_an_incomplete_managed_block(self):
        bashrc = self.home / ".bashrc"
        contents = (
            "export EDITOR=vim\n"
            "# >>> Flappy Codex PATH >>>\n"
            "export PATH=/manually/edited:$PATH\n"
            "alias keep-this='true'\n"
        )
        bashrc.write_text(contents, encoding="utf-8")

        self.run_script("uninstall.sh")

        self.assertEqual(bashrc.read_text(encoding="utf-8"), contents)


if __name__ == "__main__":
    unittest.main()
