"""A full-screen agent that owns the alternate buffer, like interactive claude.

Holds 1049h for its whole life and goes busy for a few seconds whenever a line
arrives on stdin. It repaints on SIGWINCH *only when the size actually
changed*, which is what Node's tty stream does -- Ink never sees a no-op
resize. A fixture that repainted unconditionally would hide that.

TUI_PAD inflates each busy frame so a test can build up a realistic backlog of
buffered output without waiting minutes for it.
"""

import os
import signal
import sys
import time

BUSY_SECONDS = float(os.environ.get("TUI_BUSY", "4.0"))
PAD = int(os.environ.get("TUI_PAD", "0"))
frame = 0
last_size = None


def size():
    try:
        s = os.get_terminal_size()
        return (s.lines, s.columns)
    except OSError:
        return None


def paint():
    global frame, last_size
    frame += 1
    last_size = size()
    sys.stdout.write("\x1b[2J\x1b[H" + f"TUI-FRAME {frame}")
    sys.stdout.flush()


def on_winch(*_):
    if size() != last_size:
        paint()


signal.signal(signal.SIGWINCH, on_winch)

sys.stdout.write("\x1b[?1049h")
sys.stdout.flush()
paint()

for line in sys.stdin:
    started = time.monotonic()
    while time.monotonic() - started < BUSY_SECONDS:
        sys.stdout.write("\x1b[2;1HWORKING" + ("x" * PAD))
        sys.stdout.flush()
        time.sleep(0.1)
    paint()
