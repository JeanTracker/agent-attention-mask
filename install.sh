#!/bin/sh
# Install amask, or wire it up, or take it away again (D-059).
#
#   curl -fsSL https://raw.githubusercontent.com/JeanTracker/agent-attention-mask/master/install.sh | sh
#   curl -fsSL .../install.sh | sh -s -- --uninstall
#
# With no option: clone to ~/.local/share/amask (or update the clone already
# there), link ~/.local/bin/amask to it, and add ~/.local/bin to PATH in your
# rc file if your shell does not have it. --link/--path/--uninstall are
# described by --help; wrapping an agent is `amask --wrap claude` afterwards.
# Nothing here prompts: stdin is the pipe.
#
# This file only fetches. Everything it does to your files is amask/install.py,
# run from the clone.

REPO=${AMASK_REPO:-https://github.com/JeanTracker/agent-attention-mask.git}
DEST=${AMASK_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/amask}

die() {
    printf 'amask: %s\n' "$*" >&2
    exit 1
}

# Whether this run installs; the options act alone.
installs() {
    for arg in "$@"; do
        case $arg in
            --link|--path|--uninstall|-h|--help) return 1 ;;
        esac
    done
    return 0
}

fetch() {
    command -v git >/dev/null 2>&1 || die "git is needed"
    if [ -d "$DEST/.git" ] && [ -f "$DEST/amask/cli.py" ]; then
        git -C "$DEST" pull --ff-only --quiet ||
            die "could not update $DEST (local changes?); fix it, or remove it and run this again"
        printf 'amask: updated %s\n' "$DEST"
    elif [ -e "$DEST" ]; then
        die "$DEST exists and is not an amask clone; move it away, or set AMASK_HOME"
    else
        mkdir -p "$(dirname "$DEST")" || die "cannot create $(dirname "$DEST")"
        git clone --quiet "$REPO" "$DEST" || die "could not clone $REPO"
        printf 'amask: cloned to %s\n' "$DEST"
    fi
}

# The clone whose amask/install.py runs: the installed one, else the one this
# file sits in, else a throwaway copy -- so `curl | sh -s -- --uninstall`
# works even when the installed clone is already gone.
source_dir() {
    if [ -f "$DEST/amask/install.py" ]; then
        SRC=$DEST
        return
    fi
    # Under `curl | sh`, $0 is the shell itself and says nothing about a clone.
    case $0 in */install.sh|install.sh) here=$(cd "$(dirname "$0")" && pwd) ;; *) here= ;; esac
    if [ -n "$here" ] && [ -f "$here/amask/install.py" ]; then
        SRC=$here
        return
    fi
    command -v git >/dev/null 2>&1 || die "git is needed"
    TMP=$(mktemp -d) || die "cannot make a temporary directory"
    git clone --quiet --depth 1 "$REPO" "$TMP/amask" || die "could not clone $REPO"
    SRC=$TMP/amask
}

main() {
    command -v python3 >/dev/null 2>&1 || die "python3 is needed"
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' ||
        die "python3 is older than 3.11"
    TMP=
    if installs "$@"; then
        fetch
        SRC=$DEST
        set -- --install "$@"
    else
        source_dir
    fi
    # -P: run the clone's amask, never one in the directory you stand in.
    PYTHONPATH=$SRC python3 -P -m amask.install --home "$DEST" "$@"
    status=$?
    [ -n "$TMP" ] && rm -rf "$TMP"
    exit $status
}

main "$@"
