# Contributing to Flappy Codex

Thanks for helping improve the project. Small, focused changes are easiest to
review and least likely to interfere with the Codex terminal session.

## Before opening an issue

- Search existing issues first.
- Confirm the problem still occurs on the latest `main` branch.
- Include OS, shell, terminal, Python, tmux, and Codex versions for bugs.
- Remove API keys, account details, prompts, file contents, and other private
  data from screenshots or terminal output.

Security reports do not belong in public issues. Follow [SECURITY.md](SECURITY.md)
instead.

## Development

The runtime uses only the Python standard library. From the repository root:

```bash
python3 -m unittest discover -s tests -v
bash -n install.sh uninstall.sh
python3 -m compileall -q flappycodex.py tests
```

Optional style checks use Black and Flake8:

```bash
black --check flappycodex.py tests
flake8 --max-line-length=88 --extend-ignore=E203 flappycodex.py tests
```

Tests must be non-interactive and isolated from the contributor's real Codex
configuration. Mock tmux/Codex boundaries or use temporary `HOME` and XDG
directories, as the existing tests do.

On Linux with tmux installed, the opt-in integration test creates an isolated
tmux server, drives the generated hooks with a fake Codex executable, and checks
the rendered lifecycle states and teardown:

```bash
FLAPPY_CODEX_TMUX_SMOKE=1 TERM=xterm-256color \
  python3 tests/test_tmux_integration.py -v
```

## Pull requests

1. Fork the repository and create a descriptive branch.
2. Add or update tests for behavior changes.
3. Run the commands above.
4. Explain the user-visible change and how it was verified in the pull request.

By submitting a contribution, you agree that it may be distributed under the
project's [MIT License](LICENSE).
