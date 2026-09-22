"""Drive amask inside a pty so it sees a real terminal.

The runner refuses to do anything interesting when stdout is a pipe, so every
behavioural test has to allocate a tty for it.
"""

import os
import pty
import re
import select
import signal
import struct
import fcntl
import tempfile
import termios
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(REPO, "bin", "amask")
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# The real default waits 4s of unbroken work before covering, which would make
# every case here a minute long. The behaviour under test is the state machine,
# not the constant, so the suite runs it compressed.
FAST_TIMING = {
    "AMASK_OVERLAY_DELAY": "1.0",
    # The shipped 1.5s idle threshold would make every case here seconds
    # longer; the cases that care about the real value set it themselves.
    "AMASK_IDLE_SILENCE": "0.6",
}

# How much slack the latency budgets get. 1.0 -- no slack at all -- is the
# default and what a developer's machine runs, because SC-002's 0.2s is a
# promise about the product and is not up for negotiation here.
#
# A shared CI runner is a different measurement. It is a virtual machine whose
# CPU is shared with whatever else the provider has scheduled, and on it the
# same code missed 0.2s by 68ms and took 120ms rather than under 100ms to
# uncover. Reading that as a regression would mean the suite reports the
# runner's scheduling rather than the runner's behaviour; reading it as a pass
# by lowering the number would let a real regression through on the machine
# that matters. So the number stays and CI declares that its clock is loose
# (D-049).
SLACK = max(1.0, float(os.environ.get("AMASK_TEST_SLACK", "1.0") or 1.0))


def budget(seconds):
    """A latency budget, widened by SLACK on a machine that asked for it."""
    return seconds * SLACK


# Every runner started by the suite publishes its control socket here instead
# of in the user's `~/.amask/run` (D-039). Sessions are keyed by pid, so one
# directory for the whole suite is enough.
RUN_DIR = os.path.join(tempfile.gettempdir(), "amask-test-run")
os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
FAST_TIMING["AMASK_RUN_DIR"] = RUN_DIR


# A rain cell: a cursor move, one of the rain's four colours, and exactly one
# glyph. The panel shares two of those colours by design and no longer sets a
# background, so neither the colour nor the absence of a background separates
# them any more -- the single glyph does. The panel always writes a run.
RAIN_COLOURS = rb"(?:200;255;210|110;240;140|40;190;90|12;70;35)"
RAIN_CELL = re.compile(
    rb"\x1b\[\d+;\d+H\x1b\[0;(?:1;)?38;2;" + RAIN_COLOURS + rb"m.(?=\x1b|$)",
    re.S,
)


def is_rain(chunk):
    return bool(RAIN_CELL.search(chunk))


def fixture(name, *args):
    """Command line for one of tests/fixtures/*.py."""
    return ["python3", os.path.join(FIXTURES, name)] + [str(a) for a in args]


class Result(tuple):
    """(output, exit_code), plus timing detail for the latency assertions."""

    def __new__(cls, data, code, timeline, feeds, acts=()):
        self = super().__new__(cls, (data, code))
        self.data = data
        self.code = code
        self.timeline = timeline  # [(elapsed_seconds, bytes), ...]
        self.feeds = feeds  # [(elapsed_seconds, bytes), ...]
        self.acts = list(acts)  # [(elapsed_seconds, returned_value), ...]
        return self

    def first_time(self, needle):
        """Elapsed time of the first chunk containing `needle`, or None."""
        for when, chunk in self.timeline:
            if needle in chunk:
                return when
        return None


def run_in_pty(args, feed=(), timeout=15.0, rows=24, cols=80, env=None, observe=False,
               act=()):
    """Run `amask <args>` on a pty.

    feed: iterable of (delay_seconds, bytes) written to the runner's stdin.
    act: iterable of (delay_seconds, fn); fn(pid) runs at that point and
        whatever it returns lands in `Result.acts`. This is how the
        control-socket cases talk to a running runner -- the socket is not
        stdin, so `feed` cannot reach it.

        Each one runs on its own thread, and that is not a detail: a covered
        runner writes a full rain frame every 50ms, so a loop that stops
        reading the pty while it waits for a reply fills the buffer and blocks
        the runner inside its own write. Measured -- every request made while
        the screen was covered timed out until this moved off the read loop.
    Returns a Result behaving as (output_bytes, exit_code).
    """
    pid, master = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.environ.update(FAST_TIMING)
        os.environ.update(env or {})
        try:
            os.execv(RUNNER, [RUNNER] + list(args))
        except OSError:
            os._exit(127)

    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    pending = list(feed)
    actions = list(act)
    started = time.monotonic()
    chunks = []
    timeline = []
    feeds = []
    acts = []
    threads = []
    while True:
        now = time.monotonic()
        if now - started > timeout:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            if observe:
                # Watching a long-lived agent (an interactive REPL never
                # exits): the elapsed window *is* the test, so hand back what
                # was seen instead of treating the deadline as a failure.
                os.close(master)
                return Result(b"".join(chunks), None, timeline, feeds,
                              _finish(acts, threads))
            blob = b"".join(chunks)
            raise TimeoutError(
                f"timed out after {timeout}s; {len(blob)} bytes, tail={blob[-160:]!r}"
            )

        while pending and now - started >= pending[0][0]:
            payload = pending.pop(0)[1]
            os.write(master, payload)
            feeds.append((time.monotonic() - started, payload))

        while actions and now - started >= actions[0][0]:
            fn = actions.pop(0)[1]
            slot = len(acts)
            acts.append(None)

            def runner(fn=fn, slot=slot):
                try:
                    outcome = fn(pid)
                except Exception as exc:  # the action's failure is the finding
                    outcome = exc
                acts[slot] = (time.monotonic() - started, outcome)

            thread = threading.Thread(target=runner, daemon=True)
            thread.start()
            threads.append(thread)

        readable, _, _ = select.select([master], [], [], 0.05)
        if readable:
            try:
                data = os.read(master, 65536)
            except OSError:
                data = b""
            if not data:
                break
            chunks.append(data)
            timeline.append((time.monotonic() - started, data))
        elif not pending and not actions and not any(t.is_alive() for t in threads):
            # Nothing to send and nothing to read: has the child finished?
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                _drain(master, chunks, timeline, started)
                os.close(master)
                return Result(b"".join(chunks), _code(status), timeline, feeds,
                  _finish(acts, threads))

    os.close(master)
    _, status = os.waitpid(pid, 0)
    return Result(b"".join(chunks), _code(status), timeline, feeds,
                  _finish(acts, threads))


def _finish(acts, threads):
    for thread in threads:
        thread.join(3.0)
    return [item for item in acts if item is not None]


def _drain(master, chunks, timeline, started):
    while select.select([master], [], [], 0.1)[0]:
        try:
            data = os.read(master, 65536)
        except OSError:
            return
        if not data:
            return
        chunks.append(data)
        timeline.append((time.monotonic() - started, data))


def _code(status):
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return os.WEXITSTATUS(status)
