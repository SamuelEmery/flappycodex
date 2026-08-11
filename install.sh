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
    local rc marker shell_name
    hash -r
    if [ "$(command -v codex 2>/dev/null || true)" = "$shim" ]; then
        return
    fi
    shell_name="$(basename "${SHELL:-bash}")"
    if [ "$shell_name" = zsh ]; then rc="$HOME/.zshrc"; else rc="$HOME/.bashrc"; fi
    marker="# Flappy Codex PATH ($bin_dir)"
    if [ ! -f "$rc" ] || ! grep -Fq "$marker" "$rc"; then
        {
            printf '\n%s\n' "$marker"
            printf 'export PATH=%q:$PATH\n' "$bin_dir"
        } >> "$rc"
    fi
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    printf 'Usage: ./install.sh\n\nInstalls the lightweight Flappy Codex shim for the current user.\n'
    exit 0
fi
[ "$#" -eq 0 ] || die "this installer takes no arguments"
command -v python3 >/dev/null 2>&1 || die "Python 3 is required"
original="$(find_original)"
[ -n "$original" ] || die "install the Codex CLI first, then run this installer"

if [ -e "$shim" ] && ! is_our_shim "$shim"; then
    if [ -n "${FLAPPY_CODEX_BIN_DIR:-}" ]; then
        die "$shim already exists; choose another FLAPPY_CODEX_BIN_DIR"
    fi
    bin_dir="$install_root/bin"
    shim="$bin_dir/codex"
fi

install -d "$install_root" "$bin_dir" "$(dirname "$config_file")"
install -m 0755 "$project_dir/flappycodex.py" "$installed_script"
python3 "$installed_script" --internal-configure "$original" "$config_file"
install -m 0755 "$installed_script" "$shim"
configure_path

printf 'Installed the lightweight Flappy Codex addon.\n'
printf 'Original Codex: %s\n' "$original"
if ! command -v tmux >/dev/null 2>&1; then
    printf '\nNote: tmux is required only for --flappy. Install it with your package manager.\n'
fi
printf '\nOpen a new terminal and run:\n\n  codex --flappy\n\n'
