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
import termios
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

    def __new__(cls, data, code, timeline, feeds):
        self = super().__new__(cls, (data, code))
        self.data = data
        self.code = code
        self.timeline = timeline  # [(elapsed_seconds, bytes), ...]
        self.feeds = feeds  # [(elapsed_seconds, bytes), ...]
        return self

    def first_time(self, needle):
        """Elapsed time of the first chunk containing `needle`, or None."""
        for when, chunk in self.timeline:
            if needle in chunk:
                return when
        return None


def run_in_pty(args, feed=(), timeout=15.0, rows=24, cols=80, env=None, observe=False):
    """Run `amask <args>` on a pty.

    feed: iterable of (delay_seconds, bytes) written to the runner's stdin.
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
    started = time.monotonic()
    chunks = []
    timeline = []
    feeds = []
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
                return Result(b"".join(chunks), None, timeline, feeds)
            blob = b"".join(chunks)
            raise TimeoutError(
                f"timed out after {timeout}s; {len(blob)} bytes, tail={blob[-160:]!r}"
            )

        while pending and now - started >= pending[0][0]:
            payload = pending.pop(0)[1]
            os.write(master, payload)
            feeds.append((time.monotonic() - started, payload))

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
        elif not pending:
            # Nothing to send and nothing to read: has the child finished?
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                _drain(master, chunks, timeline, started)
                os.close(master)
                return Result(b"".join(chunks), _code(status), timeline, feeds)

    os.close(master)
    _, status = os.waitpid(pid, 0)
    return Result(b"".join(chunks), _code(status), timeline, feeds)


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
