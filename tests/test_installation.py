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

    def install(self):
        bashrc = self.home / ".bashrc"
        bashrc.write_text("export PATH=/opt/tools:$PATH\n", encoding="utf-8")
        self.run_script("install.sh")
        return bashrc

    def test_custom_install_is_recorded_and_fully_reversible(self):
        bashrc = self.install()
        shim = self.bin_dir / "codex"
        config_file = self.config_home / "flappycodex" / "config.json"
        score_file = config_file.with_name("best-score.json")
        installed_script = self.data_home / "flappycodex" / "flappycodex.py"

        configuration = json.loads(config_file.read_text(encoding="utf-8"))
        self.assertEqual(configuration["shim"], str(shim.resolve()))
        self.assertTrue(shim.is_file())
        self.assertTrue(installed_script.is_file())
        self.assertIn("# >>> Flappy Codex PATH >>>", bashrc.read_text(encoding="utf-8"))
        score_file.write_text('{"best": 12}\n', encoding="utf-8")

        uninstall_environment = self.environment.copy()
        uninstall_environment.pop("FLAPPY_CODEX_BIN_DIR")
        self.run_script("uninstall.sh", environment=uninstall_environment)

        self.assertFalse(shim.exists())
        self.assertFalse(config_file.exists())
        self.assertFalse(score_file.exists())
        self.assertFalse(installed_script.exists())
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
