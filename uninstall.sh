#!/usr/bin/env bash

set -euo pipefail

data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
install_root="${FLAPPY_CODEX_HOME:-$data_home/flappycodex}"
config_dir="$config_home/flappycodex"
config_file="$config_dir/config.json"
score_file="$config_dir/best-score.json"
installed_script="$install_root/flappycodex.py"
keep_score=false

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: ./uninstall.sh [--keep-score]

Removes Flappy Codex from the current user. By default, the saved best score is
removed too. Pass --keep-score to preserve it for a future installation.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --keep-score) keep_score=true ;;
        -h|--help)
            usage
            exit 0
            ;;
        *) die "unknown option: $1" ;;
    esac
    shift
done

command -v python3 >/dev/null 2>&1 || die "Python 3 is required to uninstall"

is_our_shim() {
    local candidate="$1"
    [ "${candidate##*/}" = "codex" ] && \
        [ -f "$candidate" ] && \
        grep -Fq 'FLAPPY_CODEX_SHIM = 1' "$candidate"
}

saved_shim=""
if [ -r "$config_file" ]; then
    saved_shim="$(
        python3 -c \
            'import json,sys; print(json.load(open(sys.argv[1])).get("shim", ""))' \
            "$config_file" 2>/dev/null || true
    )"
fi

candidates=("$HOME/.local/bin/codex" "$install_root/bin/codex")
if [ -n "${FLAPPY_CODEX_BIN_DIR:-}" ]; then
    candidates+=("$FLAPPY_CODEX_BIN_DIR/codex")
fi
if [ -n "$saved_shim" ]; then
    candidates+=("$saved_shim")
fi

for candidate in "${candidates[@]}"; do
    if is_our_shim "$candidate"; then
        rm -f -- "$candidate"
    fi
done

remove_managed_path_block() {
    local rc="$1"
    [ -f "$rc" ] || return 0
    python3 - "$rc" <<'PY'
from pathlib import Path
import os
import shutil
import stat
import sys
import tempfile

requested_path = Path(sys.argv[1])
path = requested_path.resolve() if requested_path.is_symlink() else requested_path


def read_text(source):
    return source.read_text(encoding="utf-8", errors="surrogateescape")


def atomic_write(destination, contents):
    mode = stat.S_IMODE(destination.stat().st_mode) if destination.exists() else 0o600
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.flappycodex-", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(
            descriptor, "w", encoding="utf-8", errors="surrogateescape"
        ) as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def create_backup(source, destination):
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.temporary-", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        with temporary.open("rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def remove_managed_blocks(contents):
    lines = contents.splitlines()
    cleaned = []
    changed = False
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
            changed = True
            continue
        if line.startswith("# Flappy Codex PATH (") and line.endswith(")"):
            if cleaned and not cleaned[-1]:
                cleaned.pop()
            i += 1
            if i < len(lines) and lines[i].startswith("export PATH="):
                i += 1
            changed = True
            continue
        cleaned.append(line)
        i += 1
    return "\n".join(cleaned) + ("\n" if cleaned else ""), changed


original = read_text(path)
updated, changed = remove_managed_blocks(original)
backup = path.with_name(path.name + ".flappycodex.bak")
created_backup = False
if changed and updated != original:
    if not backup.exists():
        create_backup(path, backup)
        created_backup = True
    atomic_write(path, updated)

current = updated if changed else original
if backup.exists():
    if created_backup:
        backup.unlink()
    else:
        backup_contents, repaired_backup = remove_managed_blocks(read_text(backup))
        if repaired_backup:
            atomic_write(backup, backup_contents)
        if current == backup_contents:
            backup.unlink()
        else:
            print(backup)
PY
}

bash_backup="$(remove_managed_path_block "$HOME/.bashrc")"
zsh_backup="$(remove_managed_path_block "$HOME/.zshrc")"

rm -f -- "$config_file" "$installed_script"
if ! $keep_score; then
    rm -f -- "$score_file"
fi
rmdir "$config_dir" 2>/dev/null || true
rmdir "$install_root/bin" 2>/dev/null || true
rmdir "$install_root" 2>/dev/null || true

printf 'Flappy Codex removed. Your original Codex installation was not changed.\n'
if $keep_score; then
    printf 'Saved best score kept at %s.\n' "$score_file"
fi
printf 'Open a new terminal before using codex again, or refresh this shell'
printf ' (Bash: hash -r; Zsh: rehash).\n'
for backup in "$bash_backup" "$zsh_backup"; do
    if [ -n "$backup" ]; then
        printf 'Shell backup retained because your config changed: %s\n' "$backup"
    fi
done
