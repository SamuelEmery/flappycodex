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
    local candidate="$1"
    [ "${candidate##*/}" = "codex" ] && \
        [ -f "$candidate" ] && \
        grep -Fq 'FLAPPY_CODEX_SHIM = 1' "$candidate"
}

destination_conflicts() {
    local candidate="$1"
    # Never install through a symlink, even when its target has our marker.
    [ -L "$candidate" ] || \
        { [ -e "$candidate" ] && ! is_our_shim "$candidate"; }
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
    # `command -v` may resolve an exported shell function named `codex`.
    # Search PATH directly and accept only an executable file.
    current="$(type -P codex 2>/dev/null || true)"
    saved="$(saved_original)"
    if [ -n "$current" ] && [ -f "$current" ] && [ -x "$current" ] && \
        ! is_our_shim "$current"; then
        printf '%s\n' "$current"
    elif [ -n "$saved" ] && [ -f "$saved" ] && [ -x "$saved" ]; then
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
    python3 - "$rc" "$bin_dir" "$config_file" <<'PY'
from pathlib import Path
import json
import os
import shlex
import shutil
import stat
import sys
import tempfile

requested_path = Path(sys.argv[1])
path = requested_path.resolve() if requested_path.is_symlink() else requested_path
bin_dir = sys.argv[2]
config_path = Path(sys.argv[3])
requested_path_existed = requested_path.exists() or requested_path.is_symlink()
requested_path_key = str(requested_path.absolute())


def read_text(source):
    return source.read_text(encoding="utf-8", errors="surrogateescape")


def atomic_write(destination, contents):
    destination.parent.mkdir(parents=True, exist_ok=True)
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


path.parent.mkdir(parents=True, exist_ok=True)
backup = path.with_name(path.name + ".flappycodex.bak")


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
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    return "\n".join(cleaned) + ("\n" if cleaned else ""), changed


original = read_text(path) if path.exists() else ""
baseline, had_managed_block = remove_managed_blocks(original)
if path.exists() and not backup.exists():
    create_backup(path, backup)
    if had_managed_block:
        # Upgrading an older installation: store the pre-Flappy form rather
        # than backing up the already-managed PATH block.
        atomic_write(backup, baseline)
elif backup.exists():
    repaired_backup, backup_had_managed_block = remove_managed_blocks(
        read_text(backup)
    )
    if backup_had_managed_block:
        atomic_write(backup, repaired_backup)

cleaned = baseline.splitlines()
cleaned.extend(
    [
        "",
        "# >>> Flappy Codex PATH >>>",
        f"export PATH={shlex.quote(bin_dir)}:$PATH",
        "# <<< Flappy Codex PATH <<<",
    ]
)
atomic_write(path, "\n".join(cleaned) + "\n")

try:
    configuration = json.loads(read_text(config_path))
except (OSError, TypeError, json.JSONDecodeError):
    configuration = {}
if isinstance(configuration, dict):
    created = configuration.get("created_shell_configs", [])
    if not isinstance(created, list) or not all(
        isinstance(item, str) for item in created
    ):
        created = []
    if not requested_path_existed and requested_path_key not in created:
        created.append(requested_path_key)
    if created:
        configuration["created_shell_configs"] = created
        atomic_write(config_path, json.dumps(configuration, indent=2) + "\n")
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

if destination_conflicts "$shim"; then
    if [ -n "${FLAPPY_CODEX_BIN_DIR:-}" ]; then
        die "$shim already exists; choose another FLAPPY_CODEX_BIN_DIR"
    fi
    bin_dir="$install_root/bin"
    shim="$bin_dir/codex"
fi
if destination_conflicts "$shim"; then
    die "$shim already exists and is not a Flappy Codex launcher"
fi

install -d "$install_root" "$bin_dir" "$(dirname "$config_file")"
install -m 0755 "$project_dir/flappycodex.py" "$installed_script"
python3 "$installed_script" \
    --_flappycodex-internal-configure "$original" "$config_file" "$shim"
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
