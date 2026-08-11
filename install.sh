#!/usr/bin/env bash

set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
install_root="${FLAPPY_CODEX_HOME:-$data_home/flappycodex}"
default_bin="$HOME/.local/bin"
bin_dir="${FLAPPY_CODEX_BIN_DIR:-$default_bin}"
shim="$bin_dir/codex"
installed_script="$install_root/flappycodex.py"
config_file="$config_home/flappycodex/config.json"

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

is_our_shim() {
    [ -f "$1" ] && grep -q 'FLAPPY_CODEX_SHIM = 1' "$1"
}

saved_original() {
    if [ -r "$config_file" ]; then
        python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["original_codex"])' "$config_file" 2>/dev/null || true
    fi
}

saved_shim() {
    if [ -r "$config_file" ]; then
        python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("shim", ""))' "$config_file" 2>/dev/null || true
    fi
}

find_original() {
    local current saved
    current="$(command -v codex 2>/dev/null || true)"
    saved="$(saved_original)"
    if [ -n "$current" ] && ! is_our_shim "$current"; then
        printf '%s\n' "$current"
    elif [ -n "$saved" ] && [ -x "$saved" ]; then
        printf '%s\n' "$saved"
    fi
}

configure_path() {
    local rc shell_name
    hash -r
    if [ "$(command -v codex 2>/dev/null || true)" = "$shim" ]; then
        return
    fi
    shell_name="$(basename "${SHELL:-bash}")"
    case "$shell_name" in
        bash) rc="$HOME/.bashrc" ;;
        zsh) rc="$HOME/.zshrc" ;;
        *)
            printf '\nAdd this directory to PATH in your shell config:\n\n  %s\n' "$bin_dir"
            return
            ;;
    esac
    python3 - "$rc" "$bin_dir" <<'PY'
from pathlib import Path
import shlex
import sys

path = Path(sys.argv[1])
bin_dir = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
cleaned = []
i = 0
while i < len(lines):
    line = lines[i]
    if line == "# >>> Flappy Codex PATH >>>":
        try:
            end = lines.index("# <<< Flappy Codex PATH <<<", i + 1)
        except ValueError:
            cleaned.extend(lines[i:])
            break
        if cleaned and not cleaned[-1]:
            cleaned.pop()
        i = end + 1
        continue
    if line.startswith("# Flappy Codex PATH (") and line.endswith(")"):
        if cleaned and not cleaned[-1]:
            cleaned.pop()
        i += 1
        if i < len(lines) and lines[i].startswith("export PATH="):
            i += 1
        continue
    cleaned.append(line)
    i += 1

while cleaned and not cleaned[-1]:
    cleaned.pop()
cleaned.extend(
    [
        "",
        "# >>> Flappy Codex PATH >>>",
        f"export PATH={shlex.quote(bin_dir)}:$PATH",
        "# <<< Flappy Codex PATH <<<",
    ]
)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("\n".join(cleaned) + "\n", encoding="utf-8")
PY
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    printf 'Usage: ./install.sh\n\nInstalls the lightweight Flappy Codex shim for the current user.\n'
    exit 0
fi
[ "$#" -eq 0 ] || die "this installer takes no arguments"
command -v python3 >/dev/null 2>&1 || die "Python 3 is required"
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' || \
    die "Python 3.10 or newer is required"
original="$(find_original)"
[ -n "$original" ] || die "install the Codex CLI first, then run this installer"
previous_shim="$(saved_shim)"

if [ -e "$shim" ] && ! is_our_shim "$shim"; then
    if [ -n "${FLAPPY_CODEX_BIN_DIR:-}" ]; then
        die "$shim already exists; choose another FLAPPY_CODEX_BIN_DIR"
    fi
    bin_dir="$install_root/bin"
    shim="$bin_dir/codex"
fi

install -d "$install_root" "$bin_dir" "$(dirname "$config_file")"
install -m 0755 "$project_dir/flappycodex.py" "$installed_script"
python3 "$installed_script" \
    --internal-configure "$original" "$config_file" "$shim"
install -m 0755 "$installed_script" "$shim"
if [ -n "$previous_shim" ] && [ "$previous_shim" != "$shim" ] && \
    is_our_shim "$previous_shim"; then
    rm -f -- "$previous_shim"
fi
configure_path

printf 'Installed the lightweight Flappy Codex addon.\n'
printf 'Original Codex: %s\n' "$original"
if ! command -v tmux >/dev/null 2>&1; then
    printf '\nNote: tmux is required only for --flappy. Install it with your package manager.\n'
fi
printf '\nOpen a new terminal and run:\n\n  codex --flappy\n\n'
