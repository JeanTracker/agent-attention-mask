"""Real agent state, from Claude Code hooks rather than from output timing.

Everything before this inferred what the agent was doing by watching how its
output was spaced -- a heuristic that had to be retuned twice against measured
gaps and still cannot tell a long silent tool call from a finished turn.

Claude Code will simply say. It fires hooks on UserPromptSubmit, PreToolUse,
PostToolUse, Notification and Stop, and `claude --settings <json>` loads
settings *in addition to* the user's own, so the wiring lasts exactly as long
as the process and never touches anything under the user's home directory
(AGENT_AUTONOMY forbids rewriting personal config).

The events arrive on a FIFO that the runner already has in its select loop. If
the FIFO cannot be created, or the agent is not Claude Code, the channel simply
reports itself unavailable and the timing heuristic carries on as before.

Borrowed in shape from a reference project that solved the same problem the
same way (D-025).
"""

import errno
import json
import os
import secrets
import shlex
import tempfile

# What each event says about whether the agent needs the screen.
BUSY = "busy"
WAITING = "waiting"

EVENT_MEANING = {
    "UserPromptSubmit": BUSY,   # the user just handed it work
    "PreToolUse": BUSY,         # still working, and about to do something
    "PostToolUse": BUSY,
    "SubagentStop": BUSY,       # one subagent done, the turn is not
    "Notification": WAITING,    # a permission prompt or similar (SC-003)
    "Stop": WAITING,            # the turn is finished
    "SessionEnd": WAITING,
}

# Events that need a matcher in the settings fragment, per Claude Code's schema.
_MATCHED = ("PreToolUse", "PostToolUse")

# What the child is told, so any agent -- not just Claude Code -- can report
# its own state. Both halves are needed: the path says where, the token says
# who, and an event without the right token is not from this runner's agent.
ENV_VAR = "AMASK_HOOK_FIFO"
ENV_TOKEN = "AMASK_HOOK_TOKEN"


class HookChannel:
    """A FIFO carrying one JSON object per line from the agent's hooks."""

    def __init__(self, emitter, path=None, token=None):
        self.emitter = emitter
        self.path = path or os.path.join(
            tempfile.gettempdir(), f"amask-hooks-{os.getpid()}.fifo"
        )
        # Stamped into every event and checked on the way in. A FIFO is a name
        # on a filesystem, so this is what actually makes one runner deaf to
        # another one's agent (D-030).
        self.token = token or secrets.token_hex(8)
        self._read_fd = -1
        self._write_fd = -1
        self._buf = bytearray()
        self.available = False

    # -- lifecycle ---------------------------------------------------------

    def open(self):
        """Create the FIFO. Returns False if hooks are simply not possible."""
        _sweep_stale(os.path.dirname(self.path))
        try:
            if os.path.exists(self.path):
                os.unlink(self.path)
            os.mkfifo(self.path, 0o600)
            self._read_fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
            # Hold a writer open ourselves, or the reader sees EOF every time a
            # hook process exits and select() spins on a dead fd.
            self._write_fd = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)
            self.available = True
        except OSError:
            self.close()
            self.available = False
        return self.available

    def close(self):
        for fd in (self._read_fd, self._write_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._read_fd = self._write_fd = -1
        self.available = False
        try:
            if os.path.exists(self.path):
                os.unlink(self.path)
        except OSError:
            pass

    def fileno(self):
        return self._read_fd

    # -- reading -----------------------------------------------------------

    def read_events(self):
        """Drain the FIFO; return the complete JSON records found."""
        if self._read_fd < 0:
            return []
        while True:
            try:
                chunk = os.read(self._read_fd, 65536)
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                    break
                break
            if not chunk:
                break
            self._buf += chunk

        events = []
        while b"\n" in self._buf:
            line, _, rest = self._buf.partition(b"\n")
            self._buf = bytearray(rest)
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue  # a truncated or foreign line is not worth a crash
        return events

    # -- wiring ------------------------------------------------------------

    def settings(self):
        """A Claude Code settings fragment pointing every event at the emitter."""
        hooks = {}
        for event in EVENT_MEANING:
            # Quoted: the emitter and FIFO paths come from wherever the user
            # cloned to, and a folder with a space in it would otherwise split
            # into two arguments and the hook would silently never fire.
            command = " ".join(shlex.quote(part) for part in
                               (self.emitter, self.path, event, self.token))
            entry = {"hooks": [{"type": "command", "command": command}]}
            if event in _MATCHED:
                entry["matcher"] = "*"
            hooks[event] = [entry]
        return {"hooks": hooks}

    def settings_json(self):
        return json.dumps(self.settings(), separators=(",", ":"))


def _sweep_stale(directory):
    """Remove FIFOs left behind by runners that are no longer running.

    A killed runner never gets to unlink its own, and they accumulate.
    """
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if not (name.startswith("amask-hooks-") and name.endswith(".fifo")):
            continue
        try:
            pid = int(name[len("amask-hooks-"):-len(".fifo")])
        except ValueError:
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            try:
                os.unlink(os.path.join(directory, name))
            except OSError:
                pass
        except OSError:
            pass  # alive but not ours to signal
