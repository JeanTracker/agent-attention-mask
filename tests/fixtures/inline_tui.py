"""An agent that repaints in place without taking the alternate screen.

This is the shape interactive `claude` actually has: it never sends 1049h, it
just moves the cursor and redraws. Its byte stream is a picture being
maintained, so replaying it into scrollback produces garbage rather than a log.
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
    sys.stdout.write("\x1b[2J\x1b[HTUI-FRAME %d" % frame)
    sys.stdout.flush()


signal.signal(signal.SIGWINCH, lambda *_: paint() if size() != last_size else None)

paint()
for line in sys.stdin:
    started = time.monotonic()
    while time.monotonic() - started < BUSY_SECONDS:
        sys.stdout.write("\x1b[2;1HWORKING" + ("x" * PAD))
        sys.stdout.flush()
        time.sleep(0.1)
    paint()
