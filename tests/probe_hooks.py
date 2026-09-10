"""Record which Claude Code hooks actually fire, and what they carry.

The hook documentation lists far more events than a given release implements,
and the payload field names drift, so the runner's assumptions have to be
measured against the installed binary rather than read off the docs. This is
what produces those measurements.

The probe wires every event named on the command line to a dumper, runs the
agent on a real pty, feeds it a prompt, and prints the timeline. Payloads land
in a file so field names can be inspected afterwards.

    python3 tests/probe_hooks.py "Reply with exactly: ok"
    python3 tests/probe_hooks.py --interrupt 12 "Write 800 words about rain"
    python3 tests/probe_hooks.py --statusline "Reply with exactly: ok"
    python3 tests/probe_hooks.py --followup 10 "Reply with exactly: ok" \
        --total 120 "Launch a subagent that sleeps 45s, then reply: launched"

`--queued <지연> <텍스트>` submits its prompt N seconds after the first
`UserPromptSubmit` instead -- while the turn is still open, so it lands in
Claude Code's message queue rather than starting a turn of its own.

`--followup` submits a second prompt N seconds after the first turn's `Stop`.
That is the only way to observe what the session can do while background work
it started is still running -- an injected task notification arrives too soon
after `Stop` to leave a window worth watching.

Interactive Claude Code treats a fast burst of bytes as a paste, and a paste's
trailing CR lands in the input box instead of submitting, so the prompt and its
Return go out as two writes a second apart. Two of the first probe runs were
lost to that.
"""

import fcntl
import json
import os
import pty
import select
import signal
import struct
import sys
import tempfile
import termios
import time

# Everything the docs list that could plausibly say something about state.
# Events absent from the installed release simply never fire.
EVENTS = [
    "SessionStart", "SessionEnd", "UserPromptSubmit", "UserPromptExpansion",
    "Stop", "StopFailure", "Notification", "PreToolUse", "PostToolUse",
    "PostToolUseFailure", "PostToolBatch", "PermissionRequest",
    "PermissionDenied", "SubagentStart", "SubagentStop", "MessageDisplay",
    "PreCompact", "PostCompact",
]

# Claude Code's schema wants a matcher on the events that select by tool name,
# agent type, notification type and so on. An unmatched entry is dropped.
MATCHED = {
    "SessionStart", "SessionEnd", "Notification", "PreToolUse", "PostToolUse",
    "PostToolUseFailure", "PermissionRequest", "PermissionDenied",
    "SubagentStart", "SubagentStop", "PreCompact", "PostCompact",
}

DUMPER = r'''#!/usr/bin/env python3
import json, sys, time
out, event = sys.argv[1], sys.argv[2]
try:
    payload = json.loads(sys.stdin.read() or "{}")
except Exception:
    payload = {"_unparsed": True}
with open(out, "a") as f:
    f.write(json.dumps({"t": time.time(), "event": event, "payload": payload}) + "\n")
'''

STATUS_DUMPER = r'''#!/usr/bin/env python3
import json, sys, time
out = sys.argv[1]
raw = sys.stdin.read()
try:
    data = json.loads(raw)
except Exception:
    data = {"_raw": raw[:500]}
with open(out, "a") as f:
    f.write(json.dumps({"t": time.time(), "data": data}) + "\n")
print("probe")
'''

# Common fields every event carries; not worth printing on every line.
BORING = {"session_id", "transcript_path", "cwd", "permission_mode",
          "hook_event_name", "scratchpad_dir"}


def _script(body, directory, name):
    path = os.path.join(directory, name)
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, 0o755)
    return path


def probe(prompt, interrupt=None, statusline=False, total=180.0, cwd=None,
          followup=None, queued=None):
    work = tempfile.mkdtemp(prefix="probe-hooks-")
    events_path = os.path.join(work, "events.jsonl")
    status_path = os.path.join(work, "status.jsonl")
    dumper = _script(DUMPER, work, "dump.py")

    hooks = {}
    for event in EVENTS:
        entry = {"hooks": [{"type": "command",
                            "command": f"{dumper} {events_path} {event}"}]}
        if event in MATCHED:
            entry["matcher"] = "*"
        hooks[event] = [entry]
    settings = {"hooks": hooks}
    if statusline:
        status = _script(STATUS_DUMPER, work, "status.py")
        settings["statusLine"] = {"type": "command",
                                  "command": f"{status} {status_path}",
                                  "refreshInterval": 5}

    argv = ["claude", "--settings", json.dumps(settings, separators=(",", ":"))]
    pid, master = pty.fork()
    if pid == 0:
        if cwd:
            os.chdir(cwd)
        os.environ["TERM"] = "xterm-256color"
        os.execvp(argv[0], argv)
        os._exit(127)
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))

    # Typing before the input box exists loses the prompt. Startup is not a
    # fixed cost -- plugins and project settings pushed it past a minute on one
    # repo -- so the schedule is armed off SessionStart rather than off a guess,
    # and stays unarmed (with the run still draining) until the event lands.
    schedule = None
    followup_armed = followup is None
    queued_armed = queued is None

    def arm(now):
        out = [(now + 3.0, prompt.encode(), "type prompt"),
               (now + 4.2, b"\r", "submit")]
        if interrupt is not None:
            out.append((now + 4.2 + interrupt, b"\x1b", "ESC (interrupt)"))
        return out

    started = time.monotonic()
    wall0 = time.time()
    pending = []
    seen = 0
    last_output = None
    gaps = []
    while time.monotonic() - started < total:
        now = time.monotonic() - started
        if schedule is None and _has_event(events_path, "SessionStart"):
            schedule = pending = arm(now)
            print(f"{now:6.2f}  -- SessionStart seen, arming input")
        if not queued_armed and _has_event(events_path, "UserPromptSubmit"):
            # Off `UserPromptSubmit`, so the write lands while the turn is
            # open. What is being measured is the queue, and the queue only
            # exists between the prompt and its `Stop`.
            delay, text = queued
            pending += [(now + delay, text.encode(), "type queued"),
                        (now + delay + 1.2, b"\r", "submit queued")]
            queued_armed = True
            print(f"{now:6.2f}  -- UserPromptSubmit seen, queued in {delay:.1f}s")
        if not followup_armed and _has_event(events_path, "Stop"):
            # Off the first `Stop`, not off a wall-clock guess: the point of
            # the follow-up is that the main thread is idle, and only `Stop`
            # says when that began.
            delay, text = followup
            pending += [(now + delay, text.encode(), "type follow-up"),
                        (now + delay + 1.2, b"\r", "submit follow-up")]
            followup_armed = True
            print(f"{now:6.2f}  -- Stop seen, follow-up in {delay:.1f}s")
        while pending and now >= pending[0][0]:
            _, payload, label = pending.pop(0)
            os.write(master, payload)
            print(f"{now:6.2f}  -- {label}")
            sys.stdout.flush()
        readable, _, _ = select.select([master], [], [], 0.05)
        if master in readable:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            if last_output is not None:
                gaps.append(time.monotonic() - last_output)
            last_output = time.monotonic()
        seen = _drain(events_path, seen, wall0)

    _drain(events_path, seen, wall0)
    quiet = time.monotonic() - last_output if last_output else 0.0
    print(f"\noutput chunks {len(gaps) + 1}, largest gap {max(gaps or [0]):.2f}s, "
          f"quiet for {quiet:.1f}s at the end")
    print(f"payloads: {events_path}")
    if statusline and os.path.exists(status_path):
        lines = open(status_path).readlines()
        print(f"statusline ran {len(lines)} times: {status_path}")
    os.kill(pid, signal.SIGKILL)


def _has_event(path, name):
    """True once `name` shows up in the dumper's output file."""
    try:
        with open(path) as f:
            return any(f'"event": "{name}"' in line for line in f)
    except OSError:
        return False


def _drain(path, seen, wall0):
    """Print hook records that appeared since the last call."""
    if not os.path.exists(path):
        return seen
    lines = open(path).readlines()
    for line in lines[seen:]:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        fields = sorted(k for k in record["payload"] if k not in BORING)
        stamp = record["t"] - wall0
        print(f"{stamp:6.2f}  {record['event']:20s} {fields}")
    sys.stdout.flush()
    return len(lines)


def main(argv):
    interrupt = None
    statusline = False
    total = 180.0
    followup = None
    queued = None
    rest = []
    while argv:
        arg = argv.pop(0)
        if arg == "--interrupt":
            interrupt = float(argv.pop(0))
        elif arg == "--statusline":
            statusline = True
        elif arg == "--total":
            total = float(argv.pop(0))
        elif arg == "--followup":
            followup = (float(argv.pop(0)), argv.pop(0))
        elif arg == "--queued":
            queued = (float(argv.pop(0)), argv.pop(0))
        else:
            rest.append(arg)
    if not rest:
        print(__doc__.strip().splitlines()[0])
        return 2
    probe(" ".join(rest), interrupt=interrupt, statusline=statusline,
          total=total, followup=followup, queued=queued)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
