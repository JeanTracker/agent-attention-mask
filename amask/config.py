"""The stored defaults: what a *new* session starts with (D-041).

Until now the only way to change the timing was an env knob per invocation or
the control socket per session, and neither survived the session. This is one
small JSON file holding the same three values.

It applies to sessions started after it changed, and deliberately not to the
ones already running. A runner watching this file would have to answer "which
of my values did someone override for this session?" on every poll, and the
answer decides whether the screen covers -- while the thing the user actually
asked for is "the default for new sessions, and per-session when needed". The
running sessions are reachable anyway: `--top` marks several and pushes these
values into them in one keystroke.

Precedence, narrowest last:

    code default  <  this file  <  env knob  <  the socket's `set`

The env knob stays above the file because it is attached to one invocation --
`AMASK_OVERLAY_DELAY=1 amask claude` has to mean that run, whatever is stored.
"""

import json
import os

ENV_PATH = "AMASK_CONFIG"
FILE_MODE = 0o600
DIR_MODE = 0o700

# The keys this file may carry. Same three as the socket's `set`, for the same
# reason (D-036, D-039): they are the values that decide state, and a fourth
# one is a decision, not a convenience.
KEYS = ("idle_silence", "hook_stall", "overlay_delay")


def path():
    """Where the stored defaults live."""
    override = os.environ.get(ENV_PATH)
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".amask", "config.json")


def load():
    """The stored values, as a dict of the keys that are actually usable.

    A missing, unreadable or malformed file is not an error: it means "no
    stored defaults", which is the ordinary state. A file with one bad member
    still contributes its good ones -- refusing the lot would turn a typo
    into a silently reverted session.
    """
    try:
        with open(path()) as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(stored, dict):
        return {}
    out = {}
    for key in KEYS:
        if key not in stored:
            continue
        try:
            value = float(stored[key])
        except (TypeError, ValueError):
            continue
        if value != value or value in (float("inf"), float("-inf")):
            continue
        if value < 0.0:
            continue
        out[key] = value
    return out


def save(values):
    """Replace the stored defaults with `values`. Returns the written dict.

    Raises OSError if it cannot be written -- unlike the control socket, this
    is something the user asked for directly, so it has to be able to fail
    out loud.
    """
    directory = os.path.dirname(path())
    if directory:
        os.makedirs(directory, mode=DIR_MODE, exist_ok=True)
    record = {key: float(values[key]) for key in KEYS if key in values}
    tmp = path() + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path())
    return record


def merge(shipped):
    """The stored defaults over the shipped ones: what "기본 설정" means.

    `shipped` is a dict of the code's own values. This is what `--top` pushes
    into a running session, and what a new session starts from before its env
    knobs are considered.
    """
    out = dict(shipped)
    out.update(load())
    return out
