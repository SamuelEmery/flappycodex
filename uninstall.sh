#!/usr/bin/env bash

set -euo pipefail

data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
install_root="${FLAPPY_CODEX_HOME:-$data_home/flappycodex}"
config_file="$config_home/flappycodex/config.json"

candidates=("$HOME/.local/bin/codex" "$install_root/bin/codex")
if [ -n "${FLAPPY_CODEX_BIN_DIR:-}" ]; then
    candidates+=("$FLAPPY_CODEX_BIN_DIR/codex")
fi

for candidate in "${candidates[@]}"; do
    if [ -f "$candidate" ] && grep -q 'FLAPPY_CODEX_SHIM = 1' "$candidate"; then
        rm -f -- "$candidate"
    fi
done
rm -f -- "$config_file" "$install_root/flappycodex.py"
printf 'Flappy Codex removed. Your original Codex installation was not changed.\n'
